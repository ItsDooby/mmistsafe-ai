"""MistSafe AI MQTT Ingestion & Command Dispatch Worker.
Subscribes to telemetry, acknowledgements, and hardware events.
Processes the queued hardware shutdown commands with QoS 1, retain=False.
Handles reconnection with exponential backoff, JSON validation, and WAL persistence.
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict

import paho.mqtt.client as mqtt

from mistsafe.database import (
    DEFAULT_DB_PATH,
    get_queued_commands,
    handle_command_ack,
    handle_local_reset_event,
    init_db,
    log_event,
    save_telemetry,
    set_broker_status,
    update_command_status,
    utc_now_iso
)
from mistsafe.commands import is_test_device
from mistsafe.validation import ValidationError, validate_telemetry_payload

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("MistSafeWorker")

# Configuration from environment variables
DEVICE_ID = os.getenv("MISTSAFE_DEVICE_ID", "MISTSAFE-ESP32-01")
BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "127.0.0.1")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USERNAME", "").strip() or None
MQTT_PASS = os.getenv("MQTT_PASSWORD", "").strip() or None
USE_TLS = os.getenv("MQTT_USE_TLS", "false").lower() in ("true", "1", "yes")
TLS_CA_CERT = os.getenv("MQTT_TLS_CA_CERT", "").strip() or None
KEEPALIVE = int(os.getenv("MQTT_KEEPALIVE", "60"))
QOS = int(os.getenv("MQTT_QOS", "1"))
DB_PATH = os.getenv("DATABASE_PATH", DEFAULT_DB_PATH)


class MQTTWorker:
    def __init__(self):
        self.running = True
        self.device_id = DEVICE_ID
        self.db_path = DB_PATH
        self.client = None
        self.connected_to_broker = False

        init_db(self.db_path)

        # Set up Paho MQTT client (compatible with both Paho v1 and v2)
        try:
            # Paho MQTT v2 CallbackAPIVersion
            self.client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"mistsafe_worker_{os.getpid()}",
                clean_session=False
            )
        except AttributeError:
            # Paho MQTT v1 fallback
            self.client = mqtt.Client(
                client_id=f"mistsafe_worker_{os.getpid()}",
                clean_session=False
            )

        if MQTT_USER and MQTT_PASS:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)

        if USE_TLS:
            if TLS_CA_CERT and os.path.exists(TLS_CA_CERT):
                self.client.tls_set(ca_certs=TLS_CA_CERT)
            else:
                self.client.tls_set()

        # Callbacks
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, rc, *args):
        # Support both Paho v1 (rc is int) and v2 (rc is ReasonCode)
        rc_code = getattr(rc, "value", rc)
        if rc_code == 0:
            logger.info("Connected to MQTT broker at %s:%d", BROKER_HOST, BROKER_PORT)
            self.connected_to_broker = True
            set_broker_status(True, db_path=self.db_path, device_id=self.device_id)

            # Subscriptions
            topics = [
                (f"mistsafe/{self.device_id}/telemetry", QOS),
                (f"mistsafe/{self.device_id}/acks", QOS),
                (f"mistsafe/{self.device_id}/events", QOS),
                ("mistsafe/+/telemetry", QOS),  # also capture test/simulator devices
                ("mistsafe/+/acks", QOS),
                ("mistsafe/+/events", QOS)
            ]
            client.subscribe(topics)
            logger.info("Subscribed to telemetry, acks, and events topics")
            log_event(
                self.device_id,
                event_type="CONNECTION",
                severity="INFO",
                message=f"MQTT Worker connected to broker {BROKER_HOST}:{BROKER_PORT}",
                db_path=self.db_path
            )
        else:
            logger.error("MQTT connection rejected by broker, return code: %s", rc)
            self.connected_to_broker = False
            set_broker_status(False, db_path=self.db_path, device_id=self.device_id)

    def on_disconnect(self, client, userdata, rc, *args):
        self.connected_to_broker = False
        set_broker_status(False, db_path=self.db_path, device_id=self.device_id)
        logger.warning("Disconnected from MQTT broker (rc=%s)", rc)
        log_event(
            self.device_id,
            event_type="CONNECTION",
            severity="WARNING",
            message=f"MQTT Worker disconnected from broker (rc={rc})",
            db_path=self.db_path
        )

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        payload_str = ""
        try:
            payload_str = msg.payload.decode("utf-8")
        except UnicodeDecodeError:
            logger.error("Received non-UTF8 payload on %s", topic)
            return

        is_retained = getattr(msg, "retain", False)

        try:
            data = json.loads(payload_str)
        except json.JSONDecodeError as e:
            logger.error("JSON decode error on topic %s: %s", topic, e)
            log_event(
                self.device_id,
                event_type="WARNING",
                severity="WARNING",
                message=f"Rejected malformed non-JSON payload on topic {topic}",
                details={"error": str(e), "raw": payload_str[:200]},
                db_path=self.db_path
            )
            return

        # Route by topic pattern
        if topic.endswith("/telemetry"):
            self.handle_telemetry_message(topic, data, is_retained)
        elif topic.endswith("/acks"):
            self.handle_ack_message(topic, data)
        elif topic.endswith("/events"):
            self.handle_event_message(topic, data, is_retained)
        else:
            logger.debug("Ignored unhandled topic: %s", topic)

    def handle_telemetry_message(self, topic: str, data: Any, is_retained: bool):
        """Validate and store telemetry."""
        try:
            validated = validate_telemetry_payload(data)
        except ValidationError as e:
            logger.warning("Telemetry validation failed for topic %s: %s", topic, e)
            dev_id = data.get("device_id", self.device_id) if isinstance(data, dict) else self.device_id
            log_event(
                dev_id,
                event_type="WARNING",
                severity="WARNING",
                message=f"Malformed telemetry message rejected: {str(e)}",
                details={"raw": data},
                db_path=self.db_path
            )
            return

        # If message was retained in broker, log flag and do NOT mark device fresh
        if is_retained:
            logger.info("Received retained message %s; historical only", validated["message_id"])

        success, msg = save_telemetry(validated, db_path=self.db_path)
        if success:
            logger.info("Persisted telemetry msg_id=%s for %s (%s)", validated["message_id"], validated["device_id"], validated["data_origin"])
        else:
            logger.debug("Telemetry save note: %s", msg)

    def handle_ack_message(self, topic: str, data: Any):
        """Handle hardware acknowledgement."""
        if not isinstance(data, dict):
            logger.warning("ACK payload is not a JSON object")
            return

        device_id = data.get("device_id")
        if not device_id:
            # Extract from topic: mistsafe/{device_id}/acks
            parts = topic.split("/")
            if len(parts) >= 3:
                device_id = parts[1]
            else:
                device_id = self.device_id

        success, msg = handle_command_ack(device_id, data, db_path=self.db_path)
        logger.info("Hardware ACK result: %s", msg)

    def handle_event_message(self, topic: str, data: Any, is_retained: bool = False):
        """Handle hardware event, fault report, or local reset."""
        if not isinstance(data, dict):
            return
        device_id = data.get("device_id", self.device_id)
        event_type = data.get("event_type", "STATE_CHANGE")
        severity = data.get("severity", "INFO")
        message = data.get("message", "Hardware reported event")

        # Check for local reset event contract
        if event_type == "LOCAL_RESET":
            succ, reset_msg = handle_local_reset_event(data, is_retained=is_retained, db_path=self.db_path)
            logger.info("Local reset event from %s: %s (Processed: %s)", device_id, reset_msg, succ)
            return

        log_event(
            device_id=device_id,
            event_type=event_type,
            severity=severity,
            message=message,
            details=data,
            db_path=self.db_path
        )
        logger.info("Logged hardware event from %s: %s", device_id, message)

    def dispatch_queued_commands(self):
        """Check SQLite for QUEUED commands and publish with QoS 1, retain=False."""
        if not self.connected_to_broker or not self.client:
            return

        queued = get_queued_commands(db_path=self.db_path)
        for cmd in queued:
            cmd_id = cmd["command_id"]
            dev_id = cmd["device_id"]
            cmd_type = cmd["command_type"]
            topic = f"mistsafe/{dev_id}/commands"

            # Strict backend security check: RESET and START cannot be published to real device topics!
            if cmd_type in ("RESET", "START") and not is_test_device(dev_id):
                logger.error("Security lockout: Attempted %s command to real device %s rejected.", cmd_type, dev_id)
                update_command_status(
                    command_id=cmd_id,
                    status="DELIVERY_FAILED",
                    failure_reason="Forbidden: RESET/START commands are prohibited on real hardware",
                    db_path=self.db_path
                )
                continue

            command_payload = {
                "schema_version": "1.0",
                "command_id": cmd_id,
                "device_id": dev_id,
                "command": cmd_type,
                "origin": cmd["origin"],
                "stop_generation": cmd.get("stop_generation", 0),
                "created_at": cmd["created_at"],
                "expires_at": cmd["expires_at"]
            }

            try:
                publish_info = self.client.publish(
                    topic=topic,
                    payload=json.dumps(command_payload),
                    qos=1,
                    retain=False
                )
                publish_info.wait_for_publish(timeout=3.0)

                published_at = utc_now_iso()
                update_command_status(
                    command_id=cmd_id,
                    status="REQUESTED",
                    published_at=published_at,
                    db_path=self.db_path
                )
                logger.info("Published %s command %s to topic %s with QoS 1 (retain=False)", cmd_type, cmd_id, topic)
                log_event(
                    dev_id,
                    event_type=f"{cmd_type}_REQUEST",
                    severity="WARNING" if cmd_type == "STOP" else "INFO",
                    message=f"Command {cmd_id} ({cmd_type}) published to MQTT topic {topic} with QoS 1",
                    details=command_payload,
                    db_path=self.db_path
                )

            except Exception as e:
                logger.error("Failed to publish command %s: %s", cmd_id, e)
                update_command_status(
                    command_id=cmd_id,
                    status="DELIVERY_FAILED",
                    failure_reason=f"Broker publish failure: {str(e)}",
                    db_path=self.db_path
                )

    def run(self):
        """Main loop with exponential backoff for connection."""
        backoff = 1
        max_backoff = 30

        logger.info("MistSafe MQTT Worker starting. Target device: %s, Broker: %s:%d", self.device_id, BROKER_HOST, BROKER_PORT)

        while self.running:
            try:
                if not self.connected_to_broker:
                    logger.info("Attempting connection to broker %s:%d...", BROKER_HOST, BROKER_PORT)
                    try:
                        self.client.connect(BROKER_HOST, BROKER_PORT, keepalive=KEEPALIVE)
                        self.client.loop_start()
                        backoff = 1
                    except Exception as conn_err:
                        self.connected_to_broker = False
                        set_broker_status(False, db_path=self.db_path, device_id=self.device_id)
                        logger.warning("Broker connection failed: %s. Retrying in %ds...", conn_err, backoff)
                        time.sleep(backoff)
                        backoff = min(backoff * 2, max_backoff)
                        continue

                # Main worker cycle
                self.dispatch_queued_commands()
                time.sleep(1.0)

            except KeyboardInterrupt:
                logger.info("Worker interrupted by user")
                break
            except Exception as loop_err:
                logger.error("Unexpected worker loop error: %s", loop_err, exc_info=True)
                time.sleep(2.0)

        self.shutdown()

    def shutdown(self):
        """Clean shutdown of MQTT client."""
        logger.info("Shutting down MistSafe MQTT Worker...")
        self.running = False
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
        set_broker_status(False, db_path=self.db_path, device_id=self.device_id)
        logger.info("MistSafe Worker stopped.")


def main():
    worker = MQTTWorker()

    def handle_sig(sig, frame):
        logger.info("Signal %s received", sig)
        worker.running = False

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    worker.run()


if __name__ == "__main__":
    main()
