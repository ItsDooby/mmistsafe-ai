"""MistSafe AI Database Module.
Handles SQLite persistence with WAL mode, concurrency safety, schema initialization,
telemetry persistence, state tracking, command queueing, and event logging.
"""

import os
import sqlite3
import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_DB_PATH = os.getenv("DATABASE_PATH", "mistsafe.db")


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def normalize_device_id(device_id: Optional[str]) -> str:
    """Normalize device ID so case mismatches like mistsafe-sim-01 resolve canonically."""
    if not device_id or not isinstance(device_id, str):
        return ""
    dev = device_id.strip()
    if dev.upper().startswith("MISTSAFE-") or dev.upper().startswith("SIM-"):
        return dev.upper()
    return dev


class ManagedConnection(sqlite3.Connection):
    """Commit/rollback context work and release the SQLite file handle."""
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def get_db_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Create a thread-safe connection with WAL mode enabled and timeout handling."""
    conn = sqlite3.connect(db_path, timeout=10.0, check_same_thread=False, factory=ManagedConnection)
    conn.row_factory = sqlite3.Row
    # Enable Write-Ahead Logging (WAL) for high-concurrency read/write
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initialize tables, indexes, and constraints if they do not exist."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        # Telemetry records table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                message_id TEXT UNIQUE NOT NULL,
                timestamp TEXT NOT NULL,
                received_at TEXT NOT NULL,
                data_origin TEXT NOT NULL,
                temp_C REAL,
                pH REAL,
                EC_uS_cm REAL,
                turbidity_NTU REAL,
                flow_L_min REAL,
                stagnation_hours REAL,
                water_distance_cm REAL,
                sensor_status TEXT,
                uv_status TEXT,
                pump_state TEXT,
                valve_state TEXT,
                local_interlock_state TEXT,
                fault_codes TEXT,
                schema_version TEXT
            );
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_device_time ON telemetry (device_id, timestamp);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_received ON telemetry (received_at);")

        # Current device state table (stores most up-to-date state per device)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS device_state (
                device_id TEXT PRIMARY KEY,
                last_message_id TEXT,
                last_device_timestamp TEXT,
                last_received_at TEXT,
                data_origin TEXT,
                temp_C REAL,
                pH REAL,
                EC_uS_cm REAL,
                turbidity_NTU REAL,
                flow_L_min REAL,
                stagnation_hours REAL,
                water_distance_cm REAL,
                sensor_status TEXT,
                uv_status TEXT,
                pump_state TEXT,
                valve_state TEXT,
                local_interlock_state TEXT,
                fault_codes TEXT,
                manual_stop_latched INTEGER DEFAULT 0,
                manual_stop_latched_at TEXT,
                manual_stop_cleared_at TEXT,
                stop_reason TEXT,
                stop_generation INTEGER DEFAULT 0,
                current_stop_command_id TEXT,
                last_reset_status TEXT DEFAULT 'NONE',
                last_reset_event_id TEXT,
                last_reset_timestamp TEXT,
                last_reset_boot_id TEXT,
                last_reset_seq INTEGER DEFAULT 0,
                broker_connected INTEGER DEFAULT 0,
                broker_last_seen TEXT,
                device_connected INTEGER DEFAULT 0
            );
        """)

        # Per-device water-level ultrasonic calibration table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS device_calibration (
                device_id TEXT PRIMARY KEY,
                empty_distance_cm REAL NOT NULL,
                full_distance_cm REAL NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

        # Hardware commands table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS commands (
                command_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                command_type TEXT NOT NULL,
                status TEXT NOT NULL,
                origin TEXT NOT NULL,
                stop_generation INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                published_at TEXT,
                acknowledged_at TEXT,
                ack_payload TEXT,
                reported_stopped_at TEXT,
                failure_reason TEXT
            );
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_commands_device_status ON commands (device_id, status);")

        # Reset events table for audit log of local and simulator resets
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reset_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                device_id TEXT NOT NULL,
                boot_id TEXT,
                seq INTEGER,
                timestamp TEXT NOT NULL,
                stop_command_id TEXT,
                stop_generation INTEGER,
                outcome TEXT NOT NULL,
                remaining_faults TEXT,
                received_at TEXT NOT NULL,
                processed_status TEXT NOT NULL,
                details TEXT
            );
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reset_events_device ON reset_events (device_id, timestamp DESC);")

        # Events & alerts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT,
                timestamp TEXT NOT NULL
            );
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp DESC);")

        # Microscopy & sample records table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS samples (
                sample_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                collection_time TEXT NOT NULL,
                sampling_location TEXT NOT NULL,
                imaging_method TEXT NOT NULL,
                data_origin TEXT NOT NULL,
                image_path TEXT NOT NULL,
                image_safe_name TEXT NOT NULL,
                reference_lab_method TEXT,
                reference_lab_result TEXT,
                associated_telemetry_id INTEGER,
                created_at TEXT NOT NULL
            );
        """)

        # AI predictions log table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_predictions (
                prediction_id TEXT PRIMARY KEY,
                input_type TEXT NOT NULL,
                input_id TEXT NOT NULL,
                model_name TEXT NOT NULL,
                model_version TEXT NOT NULL,
                validation_status TEXT NOT NULL,
                risk_score REAL,
                inference_timestamp TEXT NOT NULL,
                reason TEXT,
                data_origin TEXT NOT NULL,
                details TEXT
            );
        """)

        # Simulator configuration and scenario state table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS simulator_config (
                device_id TEXT PRIMARY KEY,
                enabled INTEGER DEFAULT 1,
                scenario TEXT DEFAULT 'NORMAL',
                missing_sensor TEXT,
                out_of_limit_sensor TEXT,
                out_of_limit_value REAL,
                flow_loss_injected INTEGER DEFAULT 0,
                flow_loss_cycles INTEGER DEFAULT 0,
                step_count INTEGER DEFAULT 0,
                stagnation_accum_hours REAL DEFAULT 0.1,
                updated_at TEXT
            );
        """)

        # Ensure water_distance_cm exists in telemetry table
        cursor.execute("PRAGMA table_info(telemetry);")
        existing_telem_cols = {row["name"] for row in cursor.fetchall()}
        if "water_distance_cm" not in existing_telem_cols:
            try:
                cursor.execute("ALTER TABLE telemetry ADD COLUMN water_distance_cm REAL;")
            except Exception:
                pass

        # Ensure new columns exist in existing device_state table
        cursor.execute("PRAGMA table_info(device_state);")
        existing_cols = {row["name"] for row in cursor.fetchall()}
        new_cols_device_state = [
            ("stop_generation", "INTEGER DEFAULT 0"),
            ("current_stop_command_id", "TEXT"),
            ("manual_stop_cleared_at", "TEXT"),
            ("last_reset_status", "TEXT DEFAULT 'NONE'"),
            ("last_reset_event_id", "TEXT"),
            ("last_reset_timestamp", "TEXT"),
            ("last_reset_boot_id", "TEXT"),
            ("last_reset_seq", "INTEGER DEFAULT 0"),
            ("water_distance_cm", "REAL")
        ]
        for col_name, col_type in new_cols_device_state:
            if col_name not in existing_cols:
                try:
                    cursor.execute(f"ALTER TABLE device_state ADD COLUMN {col_name} {col_type};")
                except Exception:
                    pass

        # Ensure new columns exist in existing commands table
        cursor.execute("PRAGMA table_info(commands);")
        existing_cmd_cols = {row["name"] for row in cursor.fetchall()}
        if "stop_generation" not in existing_cmd_cols:
            try:
                cursor.execute("ALTER TABLE commands ADD COLUMN stop_generation INTEGER DEFAULT 0;")
            except Exception:
                pass

        simulator_cols = {row[1] for row in cursor.execute("PRAGMA table_info(simulator_config)")}
        if "water_level_mode" not in simulator_cols:
            cursor.execute("ALTER TABLE simulator_config ADD COLUMN water_level_mode TEXT NOT NULL DEFAULT '50'")

        # Seed calibration for MISTSAFE-SIM-01 test simulator device only
        # Real devices must not receive invented calibration defaults
        cursor.execute("SELECT device_id FROM device_calibration WHERE device_id = 'MISTSAFE-SIM-01'")
        if not cursor.fetchone():
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO device_calibration (device_id, empty_distance_cm, full_distance_cm, updated_at)
                    VALUES ('MISTSAFE-SIM-01', 40.0, 10.0, ?)
                """, (utc_now_iso(),))
            except Exception:
                pass

        conn.commit()


def save_telemetry(payload: Dict[str, Any], db_path: str = DEFAULT_DB_PATH) -> Tuple[bool, str]:
    """
    Persist validated telemetry record and update device state.
    Returns (success, message).
    Prevents duplicates by message_id and prevents out-of-order records
    from overwriting newer current device state.
    """
    received_at = utc_now_iso()
    device_id = normalize_device_id(payload.get("device_id"))
    message_id = payload["message_id"]
    device_timestamp = payload["timestamp"]
    data_origin = payload.get("data_origin", "REAL")

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        # Check duplicate message_id
        cursor.execute("SELECT id FROM telemetry WHERE message_id = ?", (message_id,))
        if cursor.fetchone():
            return False, f"Duplicate message_id '{message_id}' ignored"

        sensor_status_json = json.dumps(payload.get("sensor_status", {}))
        fault_codes_json = json.dumps(payload.get("fault_codes", []))

        # Insert telemetry history
        cursor.execute("""
            INSERT INTO telemetry (
                device_id, message_id, timestamp, received_at, data_origin,
                temp_C, pH, EC_uS_cm, turbidity_NTU, flow_L_min, stagnation_hours,
                water_distance_cm, sensor_status, uv_status, pump_state, valve_state,
                local_interlock_state, fault_codes, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            device_id,
            message_id,
            device_timestamp,
            received_at,
            data_origin,
            payload.get("temp_C"),
            payload.get("pH"),
            payload.get("EC_uS_cm"),
            payload.get("turbidity_NTU"),
            payload.get("flow_L_min"),
            payload.get("stagnation_hours"),
            payload.get("water_distance_cm"),
            sensor_status_json,
            payload.get("uv_status"),
            payload.get("pump_state"),
            payload.get("valve_state"),
            payload.get("local_interlock_state"),
            fault_codes_json,
            payload.get("schema_version")
        ))
        telemetry_id = cursor.lastrowid

        # Update current device state ONLY if device_timestamp is newer or state doesn't exist
        cursor.execute("SELECT last_device_timestamp, pump_state FROM device_state WHERE device_id = ?", (device_id,))
        existing_state = cursor.fetchone()

        is_newer = True
        if existing_state and existing_state["last_device_timestamp"]:
            try:
                prev_ts = datetime.fromisoformat(existing_state["last_device_timestamp"].replace("Z", "+00:00"))
                curr_ts = datetime.fromisoformat(device_timestamp.replace("Z", "+00:00"))
                if curr_ts < prev_ts:
                    is_newer = False
            except Exception:
                # If parsing fails, fall back to string comparison
                if device_timestamp < existing_state["last_device_timestamp"]:
                    is_newer = False

        if is_newer:
            cursor.execute("""
                INSERT INTO device_state (
                    device_id, last_message_id, last_device_timestamp, last_received_at, data_origin,
                    temp_C, pH, EC_uS_cm, turbidity_NTU, flow_L_min, stagnation_hours,
                    water_distance_cm, sensor_status, uv_status, pump_state, valve_state,
                    local_interlock_state, fault_codes, device_connected
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(device_id) DO UPDATE SET
                    last_message_id = excluded.last_message_id,
                    last_device_timestamp = excluded.last_device_timestamp,
                    last_received_at = excluded.last_received_at,
                    data_origin = excluded.data_origin,
                    temp_C = excluded.temp_C,
                    pH = excluded.pH,
                    EC_uS_cm = excluded.EC_uS_cm,
                    turbidity_NTU = excluded.turbidity_NTU,
                    flow_L_min = excluded.flow_L_min,
                    stagnation_hours = excluded.stagnation_hours,
                    water_distance_cm = excluded.water_distance_cm,
                    sensor_status = excluded.sensor_status,
                    uv_status = excluded.uv_status,
                    pump_state = excluded.pump_state,
                    valve_state = excluded.valve_state,
                    local_interlock_state = excluded.local_interlock_state,
                    fault_codes = excluded.fault_codes,
                    device_connected = 1
            """, (
                device_id,
                message_id,
                device_timestamp,
                received_at,
                data_origin,
                payload.get("temp_C"),
                payload.get("pH"),
                payload.get("EC_uS_cm"),
                payload.get("turbidity_NTU"),
                payload.get("flow_L_min"),
                payload.get("stagnation_hours"),
                payload.get("water_distance_cm"),
                sensor_status_json,
                payload.get("uv_status"),
                payload.get("pump_state"),
                payload.get("valve_state"),
                payload.get("local_interlock_state"),
                fault_codes_json
            ))

        # Check for state changes or hardware feedback
        reported_pump = payload.get("pump_state")
        reported_valve = payload.get("valve_state")

        # If pump is reported OFF and valve CLOSED following an acknowledged command, update command state
        if reported_pump == "OFF":
            cursor.execute("""
                UPDATE commands
                SET status = 'DEVICE_REPORTS_STOPPED',
                    reported_stopped_at = ?
                WHERE device_id = ?
                  AND status IN ('REQUESTED', 'ACKNOWLEDGED')
            """, (received_at, device_id))

        # If device was in PENDING_CONFIRMATION or RESET_REQUESTED for a reset, check if fresh feedback confirms stopped and interlock clear
        reported_interlock = payload.get("local_interlock_state")
        fault_codes = payload.get("fault_codes", [])
        if reported_pump == "OFF" and reported_valve == "CLOSED" and reported_interlock in ("NORMAL", "ARMED") and len(fault_codes) == 0:
            cursor.execute("SELECT last_reset_status, stop_generation FROM device_state WHERE device_id = ?", (device_id,))
            dev_row = cursor.fetchone()
            if dev_row and dev_row["last_reset_status"] in ("PENDING_CONFIRMATION", "RESET_REQUESTED"):
                dev_is_test = bool(device_id and (device_id.startswith("MISTSAFE-SIM-") or device_id.startswith("SIM-")))
                new_reset_status = "RESET_COMPLETE" if dev_is_test else "LOCAL_RESET_CONFIRMED"
                current_gen = dev_row["stop_generation"] or 0
                cursor.execute("""
                    UPDATE device_state
                    SET manual_stop_latched = 0,
                        manual_stop_cleared_at = ?,
                        last_reset_status = ?
                    WHERE device_id = ?
                """, (received_at, new_reset_status, device_id))
                cursor.execute("""
                    INSERT INTO events (device_id, event_type, severity, message, details, timestamp)
                    VALUES (?, 'RESET_CONFIRMED', 'INFO', ?, ?, ?)
                """, (
                    device_id,
                    'Reset complete — stopped, ready for simulated start.' if new_reset_status == "RESET_COMPLETE" else 'Local reset confirmed — equipment stopped.',
                    json.dumps({"confirmed_at": received_at, "status": new_reset_status}),
                    received_at
                ))
                # Cancel unsent STOP commands for this generation
                cursor.execute("""
                    UPDATE commands
                    SET status = 'CANCELLED_BY_RESET',
                        failure_reason = 'Cancelled by reset confirmation'
                    WHERE device_id = ?
                      AND command_type = 'STOP'
                      AND status = 'QUEUED'
                      AND stop_generation <= ?
                """, (device_id, current_gen))

        # Record faults if present
        fault_codes = payload.get("fault_codes", [])
        if fault_codes:
            for code in fault_codes:
                cursor.execute("""
                    INSERT INTO events (device_id, event_type, severity, message, details, timestamp)
                    VALUES (?, 'FAULT', 'CRITICAL', ?, ?, ?)
                """, (
                    device_id,
                    f"Reported hardware fault code: {code}",
                    json.dumps({"message_id": message_id, "telemetry_id": telemetry_id}),
                    received_at
                ))

        conn.commit()
    return True, f"Telemetry saved (ID {telemetry_id})"


def get_device_state(device_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Retrieve the current persistent state of a device."""
    dev_norm = normalize_device_id(device_id)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM device_state WHERE device_id = ? OR UPPER(device_id) = UPPER(?)", (dev_norm, dev_norm))
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["sensor_status"] = json.loads(d["sensor_status"]) if d.get("sensor_status") else {}
        except Exception:
            d["sensor_status"] = {}
        try:
            d["fault_codes"] = json.loads(d["fault_codes"]) if d.get("fault_codes") else []
        except Exception:
            d["fault_codes"] = []
        return d


def get_device_calibration(device_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Retrieve calibration for a device. Real devices have no default until set."""
    dev_norm = normalize_device_id(device_id)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT empty_distance_cm, full_distance_cm, updated_at
            FROM device_calibration
            WHERE device_id = ? OR UPPER(device_id) = UPPER(?)
        """, (dev_norm, dev_norm))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "device_id": dev_norm,
            "empty_distance_cm": row["empty_distance_cm"],
            "full_distance_cm": row["full_distance_cm"],
            "updated_at": row["updated_at"]
        }


def set_device_calibration(
    device_id: str,
    empty_distance_cm: float,
    full_distance_cm: float,
    db_path: str = DEFAULT_DB_PATH
) -> Dict[str, Any]:
    """
    Persistently store device calibration.
    Requires: empty_distance_cm > full_distance_cm.
    """
    dev_norm = normalize_device_id(device_id)
    if empty_distance_cm is None or full_distance_cm is None:
        raise ValueError("empty_distance_cm and full_distance_cm are required")
    try:
        e_val = float(empty_distance_cm)
        f_val = float(full_distance_cm)
    except (ValueError, TypeError):
        raise ValueError("empty_distance_cm and full_distance_cm must be numeric")

    if not (math.isfinite(e_val) and math.isfinite(f_val)):
        raise ValueError("Calibration values must be finite numbers")
    if e_val <= 0 or f_val < 0:
        raise ValueError("Distances must be positive numbers")
    if e_val <= f_val:
        raise ValueError("empty_distance_cm must be strictly greater than full_distance_cm")

    now_ts = utc_now_iso()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO device_calibration (device_id, empty_distance_cm, full_distance_cm, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                empty_distance_cm = excluded.empty_distance_cm,
                full_distance_cm = excluded.full_distance_cm,
                updated_at = excluded.updated_at
        """, (dev_norm, e_val, f_val, now_ts))
        conn.commit()

    return {
        "device_id": dev_norm,
        "empty_distance_cm": e_val,
        "full_distance_cm": f_val,
        "updated_at": now_ts
    }


def set_broker_status(connected: bool, db_path: str = DEFAULT_DB_PATH, device_id: Optional[str] = None) -> None:
    """Update MQTT broker connection status in SQLite."""
    now_ts = utc_now_iso()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if device_id:
            cursor.execute("""
                INSERT INTO device_state (device_id, broker_connected, broker_last_seen)
                VALUES (?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    broker_connected = excluded.broker_connected,
                    broker_last_seen = excluded.broker_last_seen
            """, (device_id, 1 if connected else 0, now_ts))
        else:
            cursor.execute("""
                UPDATE device_state
                SET broker_connected = ?,
                    broker_last_seen = ?
            """, (1 if connected else 0, now_ts))
        conn.commit()


def check_is_fresh(last_received_at: Optional[str], freshness_timeout_sec: float = 15.0) -> Tuple[bool, Optional[float]]:
    """
    Check if telemetry reading is within the freshness timeout.
    Returns (is_fresh, elapsed_seconds).
    """
    if not last_received_at:
        return False, None
    try:
        last_dt = datetime.fromisoformat(last_received_at.replace("Z", "+00:00"))
        now_dt = datetime.now(timezone.utc)
        elapsed = (now_dt - last_dt).total_seconds()
        return (elapsed <= freshness_timeout_sec), elapsed
    except Exception:
        return False, None


def latch_manual_stop(
    device_id: str,
    reason: str = "Operator manual shutdown request",
    command_id: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH
) -> int:
    """
    Persist a latched manual stop request.
    Increments stop_generation so previous reset events or commands cannot affect this stop.
    Returns the new stop_generation integer.
    """
    dev_norm = normalize_device_id(device_id)
    now_ts = utc_now_iso()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT stop_generation FROM device_state WHERE device_id = ? OR UPPER(device_id) = UPPER(?)", (dev_norm, dev_norm))
        row = cursor.fetchone()
        current_gen = row["stop_generation"] if row and row["stop_generation"] else 0
        new_gen = current_gen + 1

        cursor.execute("""
            INSERT INTO device_state (
                device_id, manual_stop_latched, manual_stop_latched_at, manual_stop_cleared_at,
                stop_reason, stop_generation, current_stop_command_id, last_reset_status
            )
            VALUES (?, 1, ?, NULL, ?, ?, ?, 'NONE')
            ON CONFLICT(device_id) DO UPDATE SET
                manual_stop_latched = 1,
                manual_stop_latched_at = excluded.manual_stop_latched_at,
                manual_stop_cleared_at = NULL,
                stop_reason = excluded.stop_reason,
                stop_generation = excluded.stop_generation,
                current_stop_command_id = excluded.current_stop_command_id,
                last_reset_status = 'NONE'
        """, (dev_norm, now_ts, reason, new_gen, command_id))

        cursor.execute("""
            INSERT INTO events (device_id, event_type, severity, message, details, timestamp)
            VALUES (?, 'STOP_REQUEST', 'CRITICAL', ?, ?, ?)
        """, (
            dev_norm,
            f"MANUAL STOP LATCHED (Gen {new_gen}): {reason}",
            json.dumps({"latched_at": now_ts, "reason": reason, "stop_generation": new_gen, "command_id": command_id}),
            now_ts
        ))
        conn.commit()
        return new_gen


def cancel_queued_stop_commands_for_generation(
    device_id: str,
    up_to_generation: int,
    db_path: str = DEFAULT_DB_PATH
) -> int:
    """
    Cancel unsent QUEUED STOP commands for a successfully reset stop generation.
    Preserves audit history by setting status to 'CANCELLED_BY_RESET'.
    Does not cancel newer stop requests (where stop_generation > up_to_generation).
    """
    now_ts = utc_now_iso()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE commands
            SET status = 'CANCELLED_BY_RESET',
                failure_reason = 'Cancelled by reset of stop generation ' || stop_generation
            WHERE device_id = ?
              AND command_type = 'STOP'
              AND status = 'QUEUED'
              AND stop_generation <= ?
        """, (device_id, up_to_generation))
        cancelled_count = cursor.rowcount
        if cancelled_count > 0:
            cursor.execute("""
                INSERT INTO events (device_id, event_type, severity, message, details, timestamp)
                VALUES (?, 'COMMAND_CANCELLED', 'INFO', ?, ?, ?)
            """, (
                device_id,
                f"Cancelled {cancelled_count} pending queued STOP command(s) for reset stop generation {up_to_generation}",
                json.dumps({"cancelled_count": cancelled_count, "up_to_generation": up_to_generation}),
                now_ts
            ))
        conn.commit()
        return cancelled_count


def clear_stop_latch(
    device_id: str,
    reset_status_text: str,
    reset_event_id: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH
) -> bool:
    """
    Clear the platform stop latch following verified reset.
    Leaves equipment stopped (pump OFF, valve CLOSED).
    Cancels any unsent queued STOP commands for the current generation.
    """
    now_ts = utc_now_iso()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT stop_generation FROM device_state WHERE device_id = ?", (device_id,))
        row = cursor.fetchone()
        current_gen = row["stop_generation"] if row and row["stop_generation"] else 0

        cursor.execute("""
            UPDATE device_state
            SET manual_stop_latched = 0,
                manual_stop_cleared_at = ?,
                last_reset_status = ?,
                last_reset_event_id = ?
            WHERE device_id = ?
        """, (now_ts, reset_status_text, reset_event_id, device_id))

        cursor.execute("""
            INSERT INTO events (device_id, event_type, severity, message, details, timestamp)
            VALUES (?, 'LATCH_CLEARED', 'INFO', ?, ?, ?)
        """, (
            device_id,
            f"Platform stop latch cleared (Gen {current_gen}): {reset_status_text}",
            json.dumps({"cleared_at": now_ts, "status": reset_status_text, "event_id": reset_event_id, "generation": current_gen}),
            now_ts
        ))
        conn.commit()

    cancel_queued_stop_commands_for_generation(device_id, current_gen, db_path=db_path)
    return True


def queue_command(
    command_id: str,
    device_id: str,
    command_type: str,
    origin: str,
    expires_in_sec: int = 30,
    stop_generation: int = 0,
    db_path: str = DEFAULT_DB_PATH
) -> None:
    """Queue a hardware command for dispatch by the MQTT worker."""
    dev_norm = normalize_device_id(device_id)
    now_dt = datetime.now(timezone.utc)
    created_at = now_dt.isoformat()
    expires_at = datetime.fromtimestamp(now_dt.timestamp() + expires_in_sec, timezone.utc).isoformat()

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO commands (
                command_id, device_id, command_type, status, origin, stop_generation, created_at, expires_at
            ) VALUES (?, ?, ?, 'QUEUED', ?, ?, ?, ?)
        """, (command_id, dev_norm, command_type, origin, stop_generation, created_at, expires_at))

        cursor.execute("""
            INSERT INTO events (device_id, event_type, severity, message, details, timestamp)
            VALUES (?, 'COMMAND_QUEUED', ?, ?, ?, ?)
        """, (
            dev_norm,
            'WARNING' if command_type == 'STOP' else 'INFO',
            f"Command {command_id} ({command_type}) queued from {origin} (Gen {stop_generation})",
            json.dumps({"command_id": command_id, "expires_at": expires_at, "origin": origin, "stop_generation": stop_generation}),
            created_at
        ))
        conn.commit()


def handle_local_reset_event(
    event_payload: Dict[str, Any],
    is_retained: bool = False,
    db_path: str = DEFAULT_DB_PATH
) -> Tuple[bool, str]:
    """
    Process local reset event published by ESP32 hardware.
    Enforces all safety invariants:
    - Retained messages rejected.
    - Duplicate event_id rejected.
    - Missing/invalid schema fields rejected.
    - Unrelated device rejected.
    - Device must be currently latched.
    - Matching stop generation or stop command ID required (cannot clear newer stop).
    - Event timestamp cannot precede latched_at timestamp.
    - Sequence number must be strictly ordered per boot_id.
    - Outcome must be SUCCESS with 0 remaining faults.
    - Device feedback must confirm interlock is NORMAL/ARMED and pump is OFF and valve is CLOSED.
    """
    now_ts = utc_now_iso()
    if is_retained:
        return False, "Rejected: Retained MQTT event cannot clear a platform latch"

    event_id = event_payload.get("event_id") or event_payload.get("reset_event_id")
    device_id = event_payload.get("device_id")
    boot_id = event_payload.get("boot_id") or event_payload.get("session_id")
    seq = event_payload.get("seq")
    timestamp = event_payload.get("timestamp")
    stop_command_id = event_payload.get("stop_command_id")
    stop_gen_reported = event_payload.get("stop_generation")
    outcome = event_payload.get("outcome", "FAILED")
    remaining_faults = event_payload.get("remaining_faults", [])

    if not event_id or not device_id or not timestamp:
        return False, "Rejected: Missing required fields (event_id, device_id, timestamp)"

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        # Check duplicate event_id
        cursor.execute("SELECT id FROM reset_events WHERE event_id = ?", (event_id,))
        if cursor.fetchone():
            return False, f"Rejected: Duplicate reset event_id '{event_id}'"

        # Check device state
        cursor.execute("SELECT * FROM device_state WHERE device_id = ?", (device_id,))
        dev_state = cursor.fetchone()
        if not dev_state:
            return False, f"Rejected: Unknown device '{device_id}'"

        dev_dict = dict(dev_state)
        manual_latched = bool(dev_dict.get("manual_stop_latched", 0))
        current_gen = dev_dict.get("stop_generation", 0)
        current_cmd_id = dev_dict.get("current_stop_command_id")
        latched_at = dev_dict.get("manual_stop_latched_at")
        last_seq = dev_dict.get("last_reset_seq", 0)
        last_boot = dev_dict.get("last_reset_boot_id")

        # 1. Device must be latched
        if not manual_latched:
            cursor.execute("""
                INSERT INTO reset_events (
                    event_id, device_id, boot_id, seq, timestamp, stop_command_id,
                    stop_generation, outcome, remaining_faults, received_at, processed_status, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'IGNORED_NOT_LATCHED', ?)
            """, (event_id, device_id, boot_id, seq, timestamp, stop_command_id, stop_gen_reported, outcome, json.dumps(remaining_faults), now_ts, json.dumps(event_payload)))
            conn.commit()
            return False, "Ignored: Device does not have an active stop latch"

        # 2. Check generation / command matching (an old reset event cannot clear a newer stop)
        if stop_gen_reported is not None and int(stop_gen_reported) < current_gen:
            cursor.execute("""
                INSERT INTO reset_events (
                    event_id, device_id, boot_id, seq, timestamp, stop_command_id,
                    stop_generation, outcome, remaining_faults, received_at, processed_status, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'REJECTED_OLD_GENERATION', ?)
            """, (event_id, device_id, boot_id, seq, timestamp, stop_command_id, stop_gen_reported, outcome, json.dumps(remaining_faults), now_ts, json.dumps(event_payload)))
            conn.commit()
            return False, f"Rejected: Event stop_generation ({stop_gen_reported}) is older than current stop_generation ({current_gen})"

        if stop_command_id and current_cmd_id and stop_command_id != current_cmd_id:
            cursor.execute("""
                INSERT INTO reset_events (
                    event_id, device_id, boot_id, seq, timestamp, stop_command_id,
                    stop_generation, outcome, remaining_faults, received_at, processed_status, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'REJECTED_MISMATCHED_COMMAND_ID', ?)
            """, (event_id, device_id, boot_id, seq, timestamp, stop_command_id, stop_gen_reported, outcome, json.dumps(remaining_faults), now_ts, json.dumps(event_payload)))
            conn.commit()
            return False, f"Rejected: Event references stop_command_id '{stop_command_id}' but current is '{current_cmd_id}'"

        # 3. Check timestamp: must not be older than latched_at
        if latched_at and timestamp < latched_at:
            cursor.execute("""
                INSERT INTO reset_events (
                    event_id, device_id, boot_id, seq, timestamp, stop_command_id,
                    stop_generation, outcome, remaining_faults, received_at, processed_status, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'REJECTED_OLD_TIMESTAMP', ?)
            """, (event_id, device_id, boot_id, seq, timestamp, stop_command_id, stop_gen_reported, outcome, json.dumps(remaining_faults), now_ts, json.dumps(event_payload)))
            conn.commit()
            return False, "Rejected: Event timestamp is older than the manual stop latch timestamp"

        # 4. Sequence number ordering check (if boot_id matches)
        if boot_id and last_boot and boot_id == last_boot and seq is not None and seq <= last_seq:
            cursor.execute("""
                INSERT INTO reset_events (
                    event_id, device_id, boot_id, seq, timestamp, stop_command_id,
                    stop_generation, outcome, remaining_faults, received_at, processed_status, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'REJECTED_OUT_OF_ORDER', ?)
            """, (event_id, device_id, boot_id, seq, timestamp, stop_command_id, stop_gen_reported, outcome, json.dumps(remaining_faults), now_ts, json.dumps(event_payload)))
            conn.commit()
            return False, f"Rejected: Out-of-order sequence number ({seq} <= {last_seq})"

        # 5. Outcome must be SUCCESS
        if outcome != "SUCCESS":
            cursor.execute("""
                INSERT INTO reset_events (
                    event_id, device_id, boot_id, seq, timestamp, stop_command_id,
                    stop_generation, outcome, remaining_faults, received_at, processed_status, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'REJECTED_OUTCOME_FAILED', ?)
            """, (event_id, device_id, boot_id, seq, timestamp, stop_command_id, stop_gen_reported, outcome, json.dumps(remaining_faults), now_ts, json.dumps(event_payload)))
            conn.commit()
            return False, f"Rejected: Local reset reported outcome '{outcome}'"

        # 6. Remaining faults must be empty
        if remaining_faults and len(remaining_faults) > 0:
            cursor.execute("""
                INSERT INTO reset_events (
                    event_id, device_id, boot_id, seq, timestamp, stop_command_id,
                    stop_generation, outcome, remaining_faults, received_at, processed_status, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'REJECTED_REMAINING_FAULTS', ?)
            """, (event_id, device_id, boot_id, seq, timestamp, stop_command_id, stop_gen_reported, outcome, json.dumps(remaining_faults), now_ts, json.dumps(event_payload)))
            conn.commit()
            return False, f"Rejected: Remaining faults present ({remaining_faults})"

        # 7. Check device reported feedback: interlock NORMAL/ARMED, pump OFF, valve CLOSED
        reported_interlock = dev_dict.get("local_interlock_state", "UNKNOWN")
        reported_pump = dev_dict.get("pump_state", "UNKNOWN")
        reported_valve = dev_dict.get("valve_state", "UNKNOWN")

        if reported_pump != "OFF" or reported_valve != "CLOSED" or reported_interlock not in ("NORMAL", "ARMED"):
            cursor.execute("""
                UPDATE device_state
                SET last_reset_status = 'PENDING_CONFIRMATION'
                WHERE device_id = ?
            """, (device_id,))
            cursor.execute("""
                INSERT INTO reset_events (
                    event_id, device_id, boot_id, seq, timestamp, stop_command_id,
                    stop_generation, outcome, remaining_faults, received_at, processed_status, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'HELD_PENDING_FEEDBACK', ?)
            """, (event_id, device_id, boot_id, seq, timestamp, stop_command_id, stop_gen_reported, outcome, json.dumps(remaining_faults), now_ts, json.dumps(event_payload)))
            conn.commit()
            return False, f"Held: Waiting for device feedback confirming interlock NORMAL, pump OFF, valve CLOSED (Current: interlock={reported_interlock}, pump={reported_pump}, valve={reported_valve})"

        # ALL CONDITIONS MET: Clear platform stop latch!
        cursor.execute("""
            UPDATE device_state
            SET manual_stop_latched = 0,
                manual_stop_cleared_at = ?,
                last_reset_status = 'LOCAL_RESET_CONFIRMED',
                last_reset_event_id = ?,
                last_reset_timestamp = ?,
                last_reset_boot_id = ?,
                last_reset_seq = ?
            WHERE device_id = ?
        """, (now_ts, event_id, timestamp, boot_id, seq if seq is not None else 0, device_id))

        cursor.execute("""
            INSERT INTO reset_events (
                event_id, device_id, boot_id, seq, timestamp, stop_command_id,
                stop_generation, outcome, remaining_faults, received_at, processed_status, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CONFIRMED', ?)
        """, (event_id, device_id, boot_id, seq, timestamp, stop_command_id, stop_gen_reported, outcome, json.dumps(remaining_faults), now_ts, json.dumps(event_payload)))

        cursor.execute("""
            INSERT INTO events (device_id, event_type, severity, message, details, timestamp)
            VALUES (?, 'LOCAL_RESET_CONFIRMED', 'INFO', 'Local reset confirmed — equipment stopped.', ?, ?)
        """, (device_id, json.dumps(event_payload), now_ts))

        conn.commit()

    cancel_queued_stop_commands_for_generation(device_id, current_gen, db_path=db_path)
    return True, "Local reset confirmed — equipment stopped."


def get_queued_commands(device_id: Optional[str] = None, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Retrieve all queued commands ready for publication."""
    dev_norm = normalize_device_id(device_id) if device_id else None
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if dev_norm:
            cursor.execute("SELECT * FROM commands WHERE status = 'QUEUED' AND (device_id = ? OR UPPER(device_id) = UPPER(?)) ORDER BY created_at ASC", (dev_norm, dev_norm))
        else:
            cursor.execute("SELECT * FROM commands WHERE status = 'QUEUED' ORDER BY created_at ASC")
        return [dict(row) for row in cursor.fetchall()]


def update_command_status(
    command_id: str,
    status: str,
    published_at: Optional[str] = None,
    failure_reason: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH
) -> None:
    """Update status of a hardware command."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if published_at:
            cursor.execute("""
                UPDATE commands
                SET status = ?, published_at = ?, failure_reason = ?
                WHERE command_id = ?
            """, (status, published_at, failure_reason, command_id))
        else:
            cursor.execute("""
                UPDATE commands
                SET status = ?, failure_reason = ?
                WHERE command_id = ?
            """, (status, failure_reason, command_id))
        conn.commit()


def handle_command_ack(
    device_id: str,
    ack_payload: Dict[str, Any],
    db_path: str = DEFAULT_DB_PATH
) -> Tuple[bool, str]:
    """
    Process incoming MQTT ACK message from ESP32.
    Matches command by command_id.
    """
    now_ts = utc_now_iso()
    command_id = ack_payload.get("command_id")
    if not command_id:
        return False, "Missing command_id in ACK"

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM commands WHERE command_id = ?", (command_id,))
        cmd = cursor.fetchone()
        if not cmd:
            return False, f"ACK received for unknown command_id '{command_id}'"

        ack_status = ack_payload.get("status", "ACKNOWLEDGED")
        rejection_reason = ack_payload.get("reason")

        new_status = "ACKNOWLEDGED" if ack_status in ("ACCEPTED", "ACKNOWLEDGED", "OK") else "DELIVERY_FAILED"
        failure_msg = rejection_reason if new_status == "DELIVERY_FAILED" else None

        cursor.execute("""
            UPDATE commands
            SET status = ?,
                acknowledged_at = ?,
                ack_payload = ?,
                failure_reason = ?
            WHERE command_id = ?
        """, (new_status, now_ts, json.dumps(ack_payload), failure_msg, command_id))

        cursor.execute("""
            INSERT INTO events (device_id, event_type, severity, message, details, timestamp)
            VALUES (?, 'COMMAND_ACK', 'INFO', ?, ?, ?)
        """, (
            device_id,
            f"{'Simulator' if device_id.upper().startswith(('MISTSAFE-SIM-', 'SIM-')) else 'Device'} ACK received for command {command_id}: {new_status}",
            json.dumps(ack_payload),
            now_ts
        ))

        # If this is a RESET command that was acknowledged, check if equipment feedback confirms reset
        if cmd["command_type"] == "RESET":
            if new_status == "ACKNOWLEDGED":
                cursor.execute("SELECT * FROM device_state WHERE device_id = ?", (device_id,))
                cur_dev = cursor.fetchone()
                if cur_dev:
                    cur_p = cur_dev["pump_state"]
                    cur_v = cur_dev["valve_state"]
                    cur_i = cur_dev["local_interlock_state"]
                    cur_f = json.loads(cur_dev["fault_codes"]) if cur_dev["fault_codes"] else []
                    # Confirm only if interlock is NORMAL/ARMED, pump is OFF, valve is CLOSED, and 0 faults
                    if cur_p == "OFF" and cur_v == "CLOSED" and cur_i in ("NORMAL", "ARMED") and len(cur_f) == 0:
                        cursor.execute("""
                            UPDATE device_state
                            SET manual_stop_latched = 0,
                                manual_stop_cleared_at = ?,
                                last_reset_status = 'RESET_COMPLETE'
                            WHERE device_id = ?
                        """, (now_ts, device_id))
                        cursor.execute("""
                            INSERT INTO events (device_id, event_type, severity, message, details, timestamp)
                            VALUES (?, 'SIMULATOR_RESET_CONFIRMED', 'INFO', 'Reset complete — stopped, ready for simulated start.', ?, ?)
                        """, (device_id, json.dumps(ack_payload), now_ts))
                    else:
                        cursor.execute("""
                            UPDATE device_state
                            SET last_reset_status = 'PENDING_CONFIRMATION'
                            WHERE device_id = ?
                        """, (device_id,))

        conn.commit()
    return True, f"Command {command_id} acknowledged as {new_status}"


def get_latest_command(device_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Get the most recent command record for a device."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM commands
            WHERE device_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (device_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def log_event(
    device_id: str,
    event_type: str,
    severity: str,
    message: str,
    details: Optional[Any] = None,
    db_path: str = DEFAULT_DB_PATH
) -> None:
    """Insert a timestamped event into the log."""
    now_ts = utc_now_iso()
    details_str = json.dumps(details) if isinstance(details, (dict, list)) else (str(details) if details else None)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO events (device_id, event_type, severity, message, details, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (device_id, event_type, severity, message, details_str, now_ts))
        conn.commit()


def get_events(
    device_id: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 100,
    db_path: str = DEFAULT_DB_PATH
) -> List[Dict[str, Any]]:
    """Retrieve event logs with optional filtering."""
    query = "SELECT * FROM events WHERE 1=1"
    params: List[Any] = []
    if device_id:
        query += " AND device_id = ?"
        params.append(device_id)
    if severity and severity != "ALL":
        query += " AND severity = ?"
        params.append(severity)
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, tuple(params))
        return [dict(r) for r in cursor.fetchall()]


def get_telemetry_history(
    device_id: str,
    hours: Optional[float] = None,
    limit: int = 1000,
    db_path: str = DEFAULT_DB_PATH
) -> List[Dict[str, Any]]:
    """Retrieve historical telemetry records."""
    dev_norm = normalize_device_id(device_id)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if hours:
            # Filter by hours in sqlite datetime
            cursor.execute("""
                SELECT * FROM telemetry
                WHERE (device_id = ? OR UPPER(device_id) = UPPER(?))
                  AND received_at >= datetime('now', '-' || ? || ' hours')
                ORDER BY received_at ASC
                LIMIT ?
            """, (dev_norm, dev_norm, str(hours), limit))
        else:
            cursor.execute("""
                SELECT * FROM telemetry
                WHERE (device_id = ? OR UPPER(device_id) = UPPER(?))
                ORDER BY received_at ASC
                LIMIT ?
            """, (dev_norm, dev_norm, limit))
        return [dict(r) for r in cursor.fetchall()]



def save_sample_record(
    sample_id: str,
    device_id: str,
    collection_time: str,
    sampling_location: str,
    imaging_method: str,
    data_origin: str,
    image_path: str,
    image_safe_name: str,
    reference_lab_method: Optional[str] = None,
    reference_lab_result: Optional[str] = None,
    associated_telemetry_id: Optional[int] = None,
    db_path: str = DEFAULT_DB_PATH
) -> None:
    """Save microscopy sample record with duplicate protection and device normalization."""
    sample_id = sample_id.strip() if sample_id else f"SMP-{int(time.time())}"
    dev_norm = normalize_device_id(device_id)
    created_at = utc_now_iso()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT sample_id FROM samples WHERE sample_id = ?", (sample_id,))
        if cursor.fetchone():
            raise ValueError(f"Sample ID '{sample_id}' already exists. Please choose a unique sample ID.")

        cursor.execute("""
            INSERT INTO samples (
                sample_id, device_id, collection_time, sampling_location,
                imaging_method, data_origin, image_path, image_safe_name,
                reference_lab_method, reference_lab_result,
                associated_telemetry_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sample_id, dev_norm, collection_time, sampling_location,
            imaging_method, data_origin, image_path, image_safe_name,
            reference_lab_method, reference_lab_result,
            associated_telemetry_id, created_at
        ))
        conn.commit()


def get_samples(device_id: Optional[str] = None, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Retrieve microscopy sample records with normalized device filtering."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if device_id and device_id.lower() != "all":
            dev_norm = normalize_device_id(device_id)
            cursor.execute("""
                SELECT * FROM samples 
                WHERE (device_id = ? OR UPPER(device_id) = UPPER(?))
                ORDER BY created_at DESC
            """, (dev_norm, dev_norm))
        else:
            cursor.execute("SELECT * FROM samples ORDER BY created_at DESC")
        return [dict(r) for r in cursor.fetchall()]


def save_ai_prediction(
    prediction_id: str,
    input_type: str,
    input_id: str,
    model_name: str,
    model_version: str,
    validation_status: str,
    risk_score: Optional[float],
    inference_timestamp: str,
    reason: Optional[str],
    data_origin: str,
    details: Optional[Any] = None,
    db_path: str = DEFAULT_DB_PATH
) -> None:
    """Save an AI model inference result."""
    details_str = json.dumps(details) if details else None
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO ai_predictions (
                prediction_id, input_type, input_id, model_name,
                model_version, validation_status, risk_score,
                inference_timestamp, reason, data_origin, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            prediction_id, input_type, input_id, model_name,
            model_version, validation_status, risk_score,
            inference_timestamp, reason, data_origin, details_str
        ))
        conn.commit()


def get_latest_ai_prediction(
    input_type: str,
    device_id: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH
) -> Optional[Dict[str, Any]]:
    """Get the most recent AI prediction for a given type."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM ai_predictions
            WHERE input_type = ?
            ORDER BY inference_timestamp DESC
            LIMIT 1
        """, (input_type,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_reset_events(
    device_id: Optional[str] = None,
    limit: int = 50,
    db_path: str = DEFAULT_DB_PATH
) -> List[Dict[str, Any]]:
    """Retrieve recorded reset event audit logs."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if device_id and device_id != "all":
            cursor.execute("""
                SELECT * FROM reset_events
                WHERE device_id = ?
                ORDER BY received_at DESC
                LIMIT ?
            """, (device_id, limit))
        else:
            cursor.execute("""
                SELECT * FROM reset_events
                ORDER BY received_at DESC
                LIMIT ?
            """, (limit,))
        return [dict(r) for r in cursor.fetchall()]


def get_simulator_config(device_id: str = "MISTSAFE-SIM-01", db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Retrieve the simulator config and scenario state for a test device."""
    dev_norm = normalize_device_id(device_id)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM simulator_config WHERE device_id = ? OR UPPER(device_id) = UPPER(?)", (dev_norm, dev_norm))
        row = cursor.fetchone()
        if not row:
            now_ts = utc_now_iso()
            cursor.execute("""
                INSERT OR IGNORE INTO simulator_config (
                    device_id, enabled, scenario, missing_sensor, out_of_limit_sensor,
                    out_of_limit_value, flow_loss_injected, flow_loss_cycles, step_count,
                    stagnation_accum_hours, updated_at
                ) VALUES (?, 1, 'NORMAL', NULL, NULL, NULL, 0, 0, 0, 0.1, ?)
            """, (dev_norm, now_ts))
            conn.commit()
            return {
                "device_id": dev_norm,
                "enabled": 1,
                "scenario": "NORMAL",
                "missing_sensor": None,
                "out_of_limit_sensor": None,
                "out_of_limit_value": None,
                "flow_loss_injected": 0,
                "flow_loss_cycles": 0,
                "step_count": 0,
                "stagnation_accum_hours": 0.1,
                "updated_at": now_ts
            }
        return dict(row)


def update_simulator_config(device_id: str, updates: Dict[str, Any], db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Update simulator config fields."""
    dev_norm = normalize_device_id(device_id)
    # Ensure row exists
    get_simulator_config(dev_norm, db_path=db_path)
    now_ts = utc_now_iso()
    updates["updated_at"] = now_ts

    allowed_cols = {
        "enabled", "scenario", "missing_sensor", "out_of_limit_sensor",
        "out_of_limit_value", "flow_loss_injected", "flow_loss_cycles",
        "step_count", "stagnation_accum_hours", "updated_at", "water_level_mode"
    }

    set_clauses = []
    params = []
    for k, v in updates.items():
        if k in allowed_cols:
            set_clauses.append(f"{k} = ?")
            params.append(v)

    if set_clauses:
        params.extend([dev_norm, dev_norm])
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE simulator_config
                SET {', '.join(set_clauses)}
                WHERE device_id = ? OR UPPER(device_id) = UPPER(?)
            """, tuple(params))
            conn.commit()

    return get_simulator_config(dev_norm, db_path=db_path)
