"""MistSafe AI Telemetry & Hardware Simulator.
Separate, explicitly launched testing utility.

Key safety constraints:
- Uses a distinct test device ID (default: MISTSAFE-SIM-01).
- Strictly sets data_origin = 'SIMULATED'.
- Prohibited from triggering real hardware commands.
- Visibly labeled as simulated in all generated payloads.
- Supports processing STOP, RESET, and START commands for test devices.
- Rejects RESET if simulated faults are active.
- Leaves equipment stopped (pump OFF, valve CLOSED) on reset.
"""

import argparse
import json
import logging
import math
import os
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from mistsafe.database import (
    DEFAULT_DB_PATH,
    get_db_connection,
    get_device_state,
    get_simulator_config,
    handle_command_ack,
    init_db,
    log_event,
    normalize_device_id,
    save_telemetry,
    update_command_status,
    update_simulator_config,
    utc_now_iso
)
from mistsafe.commands import is_test_device
from mistsafe.validation import validate_telemetry_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SIMULATOR] %(message)s")
logger = logging.getLogger("Simulator")


# Deterministic sequence tables for reproducible testing
# Starting illustrative baseline values:
# Temp: 24.0 °C, pH: 7.40, EC: 442.0 µS/cm, Turbidity: 1.67 NTU, Running flow: 13.58 L/min, Stagnation: 0.1 hrs
_TEMP_OFFSETS = [0.0, 0.12, -0.08, 0.15, -0.11, 0.05, 0.18, -0.14]
_PH_OFFSETS = [0.0, 0.02, -0.01, 0.03, -0.02, 0.01, -0.03, 0.02]
_EC_OFFSETS = [0.0, 1.2, -0.8, 1.5, -1.1, 0.9, -1.4, 0.7]
_TURB_OFFSETS = [0.0, 0.04, -0.03, 0.06, -0.02, 0.05, -0.04, 0.03]
_FLOW_OFFSETS = [0.0, 0.14, -0.11, 0.22, -0.18, 0.09, -0.15, 0.12]
_WATER_DIST_OFFSETS = [0.0, 0.4, 0.8, 0.5, 0.1, -0.3, -0.6, -0.2]


class SimulatorState:
    def __init__(self, device_id: str = "MISTSAFE-SIM-01"):
        self.device_id = normalize_device_id(device_id)
        self.pump_state = "OFF"
        self.valve_state = "CLOSED"
        self.uv_status = "OK"
        self.local_interlock_state = "NORMAL"
        self.fault_codes: List[str] = []
        self.seq = 0
        self.step_count = 0
        self.stagnation_accum_hours = 0.1

    def generate_packet(self, db_path: str = DEFAULT_DB_PATH) -> dict:
        self.seq += 1
        now_utc = utc_now_iso()
        msg_id = f"sim-{self.seq}-{uuid.uuid4().hex[:6]}"

        # Load simulator configuration
        cfg = get_simulator_config(self.device_id, db_path=db_path)
        step = cfg.get("step_count", self.seq)
        stagnation_hours = float(cfg.get("stagnation_accum_hours", 0.1))
        missing_sensor = cfg.get("missing_sensor")
        out_of_limit_sensor = cfg.get("out_of_limit_sensor")
        out_of_limit_val = cfg.get("out_of_limit_value")

        # Deterministic cycle index (8 distinct values, then wraps)
        idx = step % 8

        # 1. Evaluate Flow and Stagnation consistent with equipment state:
        # Pump OFF or valve CLOSED -> flow zero.
        # Pump ON and valve OPEN -> running flow (13.58 + offset).
        is_running = (self.pump_state == "ON" and self.valve_state == "OPEN" and "E01_FLOW_LOW" not in self.fault_codes)

        # Flow loss scenario handling
        flow_loss_injected = cfg.get("flow_loss_injected", 0)
        flow_loss_cycles = cfg.get("flow_loss_cycles", 0)

        if flow_loss_injected:
            # During flow loss injection, flow is forced to 0.0
            calc_flow = 0.0
            flow_loss_cycles += 1
            # Delay of 2 cycles before simulating flow fault and local shutdown
            if flow_loss_cycles >= 2:
                if "E01_FLOW_LOW" not in self.fault_codes:
                    self.fault_codes.append("E01_FLOW_LOW")
                self.local_interlock_state = "TRIPPED"
                self.pump_state = "OFF"
                self.valve_state = "CLOSED"
            update_simulator_config(self.device_id, {
                "flow_loss_cycles": flow_loss_cycles
            }, db_path=db_path)
        elif is_running:
            calc_flow = round(13.58 + _FLOW_OFFSETS[idx], 2)
            # Flow active resets stagnation to baseline 0.1 hours
            stagnation_hours = 0.1
        else:
            calc_flow = 0.0
            # Accumulate stagnation while stopped (+0.1 hours per 5s cycle for test progression)
            stagnation_hours = round(stagnation_hours + 0.1, 1)

        # 2. Baseline measurements with bounded reproducible variations
        temp_val = round(24.0 + _TEMP_OFFSETS[idx], 2)
        ph_val = round(7.40 + _PH_OFFSETS[idx], 2)
        ec_val = round(442.0 + _EC_OFFSETS[idx], 1)
        turb_val = round(1.67 + _TURB_OFFSETS[idx], 2)
        # Fixed test endpoints (40 cm empty, 10 cm full). No inferred level.
        level_mode = cfg.get("water_level_mode", "50")
        water_dist_val = None if level_mode in ("missing", "fault") else 40.0 - 30.0 * float(level_mode) / 100.0

        # Sensor status dict
        sensor_status = {
            "temp": "OK",
            "pH": "OK",
            "EC": "OK",
            "turbidity": "OK",
            "flow": "OK",
            "stagnation": "OK",
            "water_distance": "FAULT" if level_mode == "fault" else "UNKNOWN" if level_mode == "missing" else "OK"
        }

        # 3. Handle missing sensor injection
        if missing_sensor:
            ms = missing_sensor.lower()
            if ms in ("temp", "temp_c"):
                temp_val = None
                sensor_status["temp"] = "FAULT"
            elif ms in ("ph",):
                ph_val = None
                sensor_status["pH"] = "FAULT"
            elif ms in ("ec", "ec_us_cm"):
                ec_val = None
                sensor_status["EC"] = "FAULT"
            elif ms in ("turbidity", "turbidity_ntu"):
                turb_val = None
                sensor_status["turbidity"] = "FAULT"
            elif ms in ("flow", "flow_l_min"):
                calc_flow = None
                sensor_status["flow"] = "FAULT"
            elif ms in ("stagnation", "stagnation_hours"):
                stagnation_hours = None
                sensor_status["stagnation"] = "FAULT"
            elif ms in ("water", "water_distance", "water_distance_cm"):
                water_dist_val = None
                sensor_status["water_distance"] = "FAULT"

        # 4. Handle out-of-limit reading injection
        # Distinct prototype alert vs shutdown thresholds:
        # Alert threshold: Turbidity > 5.0 NTU (Alert) vs Turbidity > 15.0 NTU (Shutdown)
        # Temp: > 35.0 °C (Alert) vs > 50.0 °C (Shutdown)
        # pH: < 6.5 or > 8.5 (Alert) vs < 5.0 or > 10.0 (Shutdown)
        # EC: > 1000 µS/cm (Alert) vs > 2000 µS/cm (Shutdown)
        if out_of_limit_sensor and out_of_limit_val is not None:
            os_name = out_of_limit_sensor.lower()
            val_to_use = float(out_of_limit_val)
            if os_name in ("temp", "temp_c"):
                temp_val = val_to_use
                if val_to_use > 50.0:
                    self.local_interlock_state = "TRIPPED"
                    self.pump_state = "OFF"
                    self.valve_state = "CLOSED"
                    if "E05_TEMP_HIGH" not in self.fault_codes:
                        self.fault_codes.append("E05_TEMP_HIGH")
            elif os_name in ("ph",):
                ph_val = val_to_use
                if val_to_use < 5.0 or val_to_use > 10.0:
                    self.local_interlock_state = "TRIPPED"
                    self.pump_state = "OFF"
                    self.valve_state = "CLOSED"
                    if "E06_PH_CRITICAL" not in self.fault_codes:
                        self.fault_codes.append("E06_PH_CRITICAL")
            elif os_name in ("ec", "ec_us_cm"):
                ec_val = val_to_use
                if val_to_use > 2000.0:
                    self.local_interlock_state = "TRIPPED"
                    self.pump_state = "OFF"
                    self.valve_state = "CLOSED"
                    if "E07_EC_HIGH" not in self.fault_codes:
                        self.fault_codes.append("E07_EC_HIGH")
            elif os_name in ("turbidity", "turbidity_ntu"):
                turb_val = val_to_use
                if val_to_use > 15.0:
                    self.local_interlock_state = "TRIPPED"
                    self.pump_state = "OFF"
                    self.valve_state = "CLOSED"
                    if "E04_TURBIDITY_HIGH" not in self.fault_codes:
                        self.fault_codes.append("E04_TURBIDITY_HIGH")
            elif os_name in ("flow", "flow_l_min"):
                calc_flow = val_to_use
            elif os_name in ("stagnation", "stagnation_hours"):
                stagnation_hours = val_to_use
            elif os_name in ("water", "water_distance", "water_distance_cm"):
                water_dist_val = val_to_use

        # If UV status is FAULT, local interlock stops pump and closes valve
        if self.uv_status == "FAULT" or "E02_UV_FAIL" in self.fault_codes:
            self.pump_state = "OFF"
            self.valve_state = "CLOSED"
            self.local_interlock_state = "TRIPPED"
            if "E02_UV_FAIL" not in self.fault_codes:
                self.fault_codes.append("E02_UV_FAIL")
            calc_flow = 0.0

        # Advance step count & stagnation in persistent config
        update_simulator_config(self.device_id, {
            "step_count": step + 1,
            "stagnation_accum_hours": stagnation_hours if stagnation_hours is not None else 0.1
        }, db_path=db_path)

        return {
            "schema_version": "1.0",
            "device_id": self.device_id,
            "message_id": msg_id,
            "timestamp": now_utc,
            "data_origin": "SIMULATED",  # MANDATORY SAFETY LABEL
            "temp_C": temp_val,
            "pH": ph_val,
            "EC_uS_cm": ec_val,
            "turbidity_NTU": turb_val,
            "flow_L_min": calc_flow,
            "stagnation_hours": stagnation_hours,
            "water_distance_cm": water_dist_val,
            "sensor_status": sensor_status,
            "uv_status": self.uv_status,
            "pump_state": self.pump_state,
            "valve_state": self.valve_state,
            "local_interlock_state": self.local_interlock_state,
            "fault_codes": list(self.fault_codes)
        }


    def process_command(self, cmd: Dict[str, Any], db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
        cmd_id = cmd["command_id"]
        cmd_type = cmd["command_type"]
        now_ts = utc_now_iso()

        if cmd_type == "STOP":
            self.pump_state = "OFF"
            self.valve_state = "CLOSED"
            self.local_interlock_state = "TRIPPED"
            ack = {
                "command_id": cmd_id,
                "device_id": self.device_id,
                "status": "ACCEPTED",
                "timestamp": now_ts,
                "action": "SIMULATOR_STOPPED",
                "reason": None
            }
            logger.info("Simulator executed STOP: pump=OFF, valve=CLOSED, interlock=TRIPPED")

        elif cmd_type == "RESET":
            if self.fault_codes and len(self.fault_codes) > 0:
                ack = {
                    "command_id": cmd_id,
                    "device_id": self.device_id,
                    "status": "REJECTED",
                    "timestamp": now_ts,
                    "action": "RESET_REJECTED",
                    "reason": f"ACTIVE_FAULTS_PRESENT: {self.fault_codes}"
                }
                logger.warning("Simulator rejected RESET because faults are active: %s", self.fault_codes)
            else:
                # Reset leaves equipment STOPPED!
                self.pump_state = "OFF"
                self.valve_state = "CLOSED"
                self.local_interlock_state = "NORMAL"
                ack = {
                    "command_id": cmd_id,
                    "device_id": self.device_id,
                    "status": "ACCEPTED",
                    "timestamp": now_ts,
                    "action": "INTERLOCK_RESET_STOPPED",
                    "reason": None
                }
                logger.info("Simulator executed RESET: interlock=NORMAL, pump=OFF, valve=CLOSED")

        elif cmd_type == "START":
            if self.fault_codes and len(self.fault_codes) > 0:
                ack = {
                    "command_id": cmd_id,
                    "device_id": self.device_id,
                    "status": "REJECTED",
                    "timestamp": now_ts,
                    "action": "START_REJECTED",
                    "reason": f"ACTIVE_FAULTS_PRESENT: {self.fault_codes}"
                }
                logger.warning("Simulator rejected START because faults are active: %s", self.fault_codes)
            elif self.local_interlock_state != "NORMAL":
                ack = {
                    "command_id": cmd_id,
                    "device_id": self.device_id,
                    "status": "REJECTED",
                    "timestamp": now_ts,
                    "action": "START_REJECTED",
                    "reason": f"INTERLOCK_NOT_READY ({self.local_interlock_state})"
                }
                logger.warning("Simulator rejected START because interlock is not NORMAL: %s", self.local_interlock_state)
            else:
                self.pump_state = "ON"
                self.valve_state = "OPEN"
                ack = {
                    "command_id": cmd_id,
                    "device_id": self.device_id,
                    "status": "ACCEPTED",
                    "timestamp": now_ts,
                    "action": "OPERATION_STARTED",
                    "reason": None
                }
                logger.info("Simulator executed START: pump=ON, valve=OPEN")
        else:
            ack = {
                "command_id": cmd_id,
                "device_id": self.device_id,
                "status": "REJECTED",
                "timestamp": now_ts,
                "action": "UNKNOWN_COMMAND",
                "reason": f"Unsupported command {cmd_type}"
            }

        handle_command_ack(self.device_id, ack, db_path=db_path)
        return ack


def check_and_execute_pending_simulator_commands(sim: SimulatorState, db_path: str = DEFAULT_DB_PATH):
    """Check database for pending commands addressed to this test device and execute them."""
    dev_norm = normalize_device_id(getattr(sim, "device_id", "MISTSAFE-SIM-01"))
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM commands
            WHERE (device_id = ? OR UPPER(device_id) = UPPER(?))
              AND status IN ('QUEUED', 'REQUESTED')
            ORDER BY created_at ASC
        """, (dev_norm, dev_norm))
        pending = [dict(r) for r in cursor.fetchall()]

    for cmd in pending:
        sim.process_command(cmd, db_path=db_path)


def execute_simulator_cycle_for_device(
    device_id: str,
    db_path: str = DEFAULT_DB_PATH
) -> Tuple[bool, Optional[SimulatorState]]:
    """
    Load current state of test device, check and execute any pending simulator commands
    (STOP, RESET, START), generate updated telemetry packet, and persist to database.
    Strictly isolated: returns (False, None) immediately if device_id is not a test device.
    """
    dev_norm = normalize_device_id(device_id)
    if not is_test_device(dev_norm):
        return False, None

    sim = SimulatorState(device_id=dev_norm)
    st = get_device_state(dev_norm, db_path=db_path)
    if st:
        sim.pump_state = st.get("pump_state", "OFF")
        sim.valve_state = st.get("valve_state", "CLOSED")
        sim.uv_status = st.get("uv_status", "OK")
        sim.fault_codes = st.get("fault_codes", [])
        sim.local_interlock_state = st.get("local_interlock_state", "NORMAL")

    check_and_execute_pending_simulator_commands(sim, db_path=db_path)
    packet = sim.generate_packet(db_path=db_path)
    save_telemetry(packet, db_path=db_path)
    return True, sim


def main():
    parser = argparse.ArgumentParser(description="MistSafe AI Synthetic Telemetry Generator & Test Runner")
    parser.add_argument("--device-id", default="MISTSAFE-SIM-01", help="Test device identifier")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between packets")
    parser.add_argument("--count", type=int, default=10, help="Number of packets to emit (0 for infinite)")
    parser.add_argument("--mqtt", action="store_true", help="Publish via MQTT broker instead of direct SQLite")
    parser.add_argument("--fault", help="Inject comma-separated simulated fault codes (e.g. E01_FLOW_LOW,E02_UV_FAIL)")
    parser.add_argument("--pump-on", action="store_true", help="Initial pump state ON (default is OFF)")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Path to SQLite database")

    args = parser.parse_args()

    # Safety check: enforce SIM prefix or distinct test name
    if not is_test_device(args.device_id):
        print(f"ERROR: Cannot use production device ID '{args.device_id}' for simulator.")
        print("Simulator must use a dedicated test device ID (e.g. MISTSAFE-SIM-01).")
        return

    print("=" * 60)
    print("MISTSAFE AI TELEMETRY SIMULATOR (TEST UTILITY ONLY)")
    print(f"Target Device:  {args.device_id}")
    print(f"Data Origin:    SIMULATED")
    print(f"Mode:           {'MQTT Broker' if args.mqtt else 'Direct SQLite'}")
    print(f"Packet Count:   {args.count if args.count > 0 else 'Continuous'}")
    print("WARNING: Simulated data CANNOT trigger real hardware shutdown.")
    print("=" * 60)

    init_db(args.db_path)
    sim = SimulatorState(device_id=args.device_id)
    if args.pump_on:
        sim.pump_state = "ON"
        sim.valve_state = "OPEN"
    if args.fault:
        sim.fault_codes = [f.strip() for f in args.fault.split(",") if f.strip()]
        sim.local_interlock_state = "TRIPPED"
        logger.warning("Simulated faults injected: %s", sim.fault_codes)

    mqtt_client = None
    if args.mqtt:
        import paho.mqtt.client as mqtt
        broker_host = os.getenv("MQTT_BROKER_HOST", "127.0.0.1")
        broker_port = int(os.getenv("MQTT_BROKER_PORT", "1883"))
        mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2 if hasattr(mqtt, "CallbackAPIVersion") else "sim_pub")

        def on_msg(client, userdata, msg):
            try:
                data = json.loads(msg.payload.decode("utf-8"))
                sim.process_command(data, db_path=args.db_path)
            except Exception as e:
                logger.error("Error processing MQTT command in simulator: %s", e)

        mqtt_client.on_message = on_msg
        try:
            mqtt_client.connect(broker_host, broker_port, 60)
            mqtt_client.subscribe(f"mistsafe/{args.device_id}/commands", 1)
            mqtt_client.loop_start()
        except Exception as e:
            logger.warning("Could not connect to MQTT broker (%s). Falling back to direct DB mode.", e)
            mqtt_client = None

    emitted = 0
    try:
        while True:
            # Check and execute any pending commands (e.g. RESET, START, STOP)
            check_and_execute_pending_simulator_commands(sim, db_path=args.db_path)

            packet = sim.generate_packet()
            emitted += 1

            if args.mqtt and mqtt_client:
                topic = f"mistsafe/{args.device_id}/telemetry"
                mqtt_client.publish(topic, json.dumps(packet), qos=1)
                logger.info("Published SIMULATED packet #%d to MQTT topic %s (pump=%s, valve=%s)", emitted, topic, sim.pump_state, sim.valve_state)
            else:
                validated = validate_telemetry_payload(packet)
                success, msg = save_telemetry(validated, db_path=args.db_path)
                logger.info("Inserted SIMULATED packet #%d to SQLite (pump=%s, valve=%s, interlock=%s)", emitted, sim.pump_state, sim.valve_state, sim.local_interlock_state)

            if args.count > 0 and emitted >= args.count:
                break
            time.sleep(args.interval)

    except KeyboardInterrupt:
        logger.info("Simulator stopped by operator.")
    finally:
        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()


if __name__ == "__main__":
    main()
