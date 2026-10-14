"""MistSafe AI Commands & Hardware Safety Module.
Manages STOP requests, latched operator shutdowns, command queuing,
simulator test controls, expiry enforcement, acknowledgement tracking,
and physical feedback verification.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from mistsafe.database import (
    DEFAULT_DB_PATH,
    check_is_fresh,
    get_db_connection,
    get_device_state,
    get_latest_command,
    latch_manual_stop,
    normalize_device_id,
    queue_command,
    update_command_status,
    utc_now_iso
)

DEFAULT_EXPIRY_SEC = 30


class CommandState:
    STOP_QUEUED = "STOP QUEUED"
    STOP_REQUESTED = "STOP REQUESTED"
    RESET_QUEUED = "RESET QUEUED"
    RESET_REQUESTED = "RESET REQUESTED"
    START_QUEUED = "START QUEUED"
    START_REQUESTED = "START REQUESTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DEVICE_REPORTS_STOPPED = "DEVICE REPORTS STOPPED"
    DELIVERY_FAILED = "DELIVERY FAILED"
    ACK_TIMEOUT = "ACK TIMEOUT"
    CANCELLED_BY_RESET = "CANCELLED BY RESET"


def is_test_device(device_id: str) -> bool:
    """Check if device is an explicitly configured test/simulator device."""
    dev_norm = normalize_device_id(device_id)
    return dev_norm.startswith("MISTSAFE-SIM-") or dev_norm.startswith("SIM-")


def request_shutdown(
    device_id: str,
    origin: str = "MANUAL",
    reason: str = "Operator manual emergency stop requested from dashboard",
    expires_in_sec: int = DEFAULT_EXPIRY_SEC,
    db_path: str = DEFAULT_DB_PATH
) -> Tuple[bool, str, str]:
    """
    Initiate a shutdown request.
    1. Generates unique command ID ('cmd-<uuid4>').
    2. Persists a latched manual stop request with incremented stop_generation.
    3. Queues STOP command for MQTT worker with expiry timestamp and stop_generation.
    Deduplicates active pending STOP commands for this generation.
    Returns (success, command_id, message).
    """
    device_id = normalize_device_id(device_id)
    command_id = f"cmd-{uuid.uuid4().hex[:12]}"

    dev_state = get_device_state(device_id, db_path=db_path)
    reset_in_progress = dev_state and dev_state.get("last_reset_status") in (
        "RESET_REQUESTED", "PENDING_CONFIRMATION", "RESET_COMPLETE", "LOCAL_RESET_CONFIRMED"
    )

    # Check deduplication: if there is already a QUEUED or recently REQUESTED stop command that hasn't timed out,
    # UNLESS a reset was in progress (in which case the new stop request takes precedence)
    if not reset_in_progress:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT command_id, status, created_at, expires_at, stop_generation
                FROM commands
                WHERE device_id = ?
                  AND command_type = 'STOP'
                  AND status IN ('QUEUED', 'REQUESTED')
                ORDER BY created_at DESC
                LIMIT 1
            """, (device_id,))
            active_cmd = cursor.fetchone()
            if active_cmd:
                cmd_id = active_cmd["command_id"]
                try:
                    exp_dt = datetime.fromisoformat(active_cmd["expires_at"].replace("Z", "+00:00"))
                    if datetime.now(timezone.utc) < exp_dt:
                        return False, cmd_id, f"Active STOP command '{cmd_id}' is already {active_cmd['status']}."
                except Exception:
                    pass

    # Latch manual stop and get new stop_generation
    new_gen = latch_manual_stop(device_id, reason=reason, command_id=command_id, db_path=db_path)

    # Queue the STOP command with generation
    queue_command(
        command_id=command_id,
        device_id=device_id,
        command_type="STOP",
        origin=origin,
        expires_in_sec=expires_in_sec,
        stop_generation=new_gen,
        db_path=db_path
    )

    return True, command_id, f"STOP command '{command_id}' queued successfully (Gen {new_gen}, expires in {expires_in_sec}s)."


def request_simulator_reset(
    device_id: str,
    db_path: str = DEFAULT_DB_PATH
) -> Tuple[bool, str, str]:
    """
    Reset test device simulator.
    Safety constraints enforced in backend:
    - Restricted to explicitly configured test devices only (cannot target real hardware).
    - Rejects reset while active faults remain present.
    - Queues uniquely identified RESET command.
    - Equipment remains stopped (pump OFF, valve CLOSED).
    """
    device_id = normalize_device_id(device_id)
    if not is_test_device(device_id):
        return False, "", f"Forbidden: Simulator controls are restricted to test devices only. Cannot target hardware '{device_id}'."

    dev_state = get_device_state(device_id, db_path=db_path)
    if dev_state and dev_state.get("fault_codes") and len(dev_state["fault_codes"]) > 0:
        return False, "", f"Cannot reset test device: Active faults present: {dev_state['fault_codes']}. Resolve simulated faults first."

    # Check deduplication for active pending RESET commands
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT command_id, status, expires_at
            FROM commands
            WHERE device_id = ?
              AND command_type = 'RESET'
              AND status IN ('QUEUED', 'REQUESTED')
            ORDER BY created_at DESC
            LIMIT 1
        """, (device_id,))
        active_rst = cursor.fetchone()
        if active_rst:
            cmd_id = active_rst["command_id"]
            try:
                exp_dt = datetime.fromisoformat(active_rst["expires_at"].replace("Z", "+00:00"))
                if datetime.now(timezone.utc) < exp_dt:
                    return False, cmd_id, f"Active RESET command '{cmd_id}' is already {active_rst['status']}."
            except Exception:
                pass

    current_gen = dev_state.get("stop_generation", 0) if dev_state else 0
    command_id = f"cmd-rst-{uuid.uuid4().hex[:10]}"

    queue_command(
        command_id=command_id,
        device_id=device_id,
        command_type="RESET",
        origin="SIMULATOR_CONTROL",
        expires_in_sec=30,
        stop_generation=current_gen,
        db_path=db_path
    )

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE device_state
            SET last_reset_status = 'RESET_REQUESTED'
            WHERE device_id = ?
        """, (device_id,))
        conn.commit()

    return True, command_id, f"Reset command '{command_id}' queued for test device. Equipment remains stopped."


def request_simulator_start(
    device_id: str,
    db_path: str = DEFAULT_DB_PATH
) -> Tuple[bool, str, str]:
    """
    Start simulated operation on test device.
    Safety constraints enforced in backend:
    - Restricted to explicitly configured test devices only.
    - Separate explicit action from reset.
    - Requires successful reset (platform stop latch cleared).
    - Requires fresh device feedback and no active faults or stop requests.
    - State is updated only from simulator response.
    """
    device_id = normalize_device_id(device_id)
    if not is_test_device(device_id):
        return False, "", f"Forbidden: Simulator controls are restricted to test devices only. Cannot target hardware '{device_id}'."

    dev_state = get_device_state(device_id, db_path=db_path)
    if not dev_state:
        return False, "", "Cannot start: Test device has no recorded state."

    # Must not have an active stop latch
    if dev_state.get("manual_stop_latched"):
        return False, "", "Cannot start: Platform stop latch is active. Reset test device first."

    # Must have no active faults
    if dev_state.get("fault_codes") and len(dev_state["fault_codes"]) > 0:
        return False, "", f"Cannot start: Active faults present: {dev_state['fault_codes']}."

    # Must have fresh device feedback
    is_fresh, elapsed = check_is_fresh(dev_state.get("last_received_at"), freshness_timeout_sec=30.0)
    if not is_fresh:
        return False, "", f"Cannot start: Simulator feedback is stale or missing ({elapsed}s ago)."

    # Interlock must be NORMAL or ARMED
    interlock = dev_state.get("local_interlock_state", "UNKNOWN")
    if interlock not in ("NORMAL", "ARMED"):
        return False, "", f"Cannot start: Device interlock is in {interlock} state."

    # Check deduplication
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT command_id, status, expires_at
            FROM commands
            WHERE device_id = ?
              AND command_type = 'START'
              AND status IN ('QUEUED', 'REQUESTED')
            ORDER BY created_at DESC
            LIMIT 1
        """, (device_id,))
        active_strt = cursor.fetchone()
        if active_strt:
            cmd_id = active_strt["command_id"]
            try:
                exp_dt = datetime.fromisoformat(active_strt["expires_at"].replace("Z", "+00:00"))
                if datetime.now(timezone.utc) < exp_dt:
                    return False, cmd_id, f"Active START command '{cmd_id}' is already {active_strt['status']}."
            except Exception:
                pass

    current_gen = dev_state.get("stop_generation", 0)
    command_id = f"cmd-strt-{uuid.uuid4().hex[:10]}"

    queue_command(
        command_id=command_id,
        device_id=device_id,
        command_type="START",
        origin="SIMULATOR_CONTROL",
        expires_in_sec=30,
        stop_generation=current_gen,
        db_path=db_path
    )

    return True, command_id, f"START command '{command_id}' queued for test simulator."


def evaluate_hardware_safety_status(
    device_id: str,
    db_path: str = DEFAULT_DB_PATH
) -> Dict[str, Any]:
    """
    Separately compute:
    - Platform stop latch
    - Device-reported interlock
    - Command progress
    - Reported pump and valve states
    - Reset status & banner message
    Never combines these into a single misleading status.
    """
    device_id = normalize_device_id(device_id)
    dev_state = get_device_state(device_id, db_path=db_path) or {}
    latest_cmd = get_latest_command(device_id, db_path=db_path)

    broker_connected = bool(dev_state.get("broker_connected", 0))
    manual_stop_latched = bool(dev_state.get("manual_stop_latched", 0))
    stop_reason = dev_state.get("stop_reason")
    stop_generation = dev_state.get("stop_generation", 0)
    last_reset_status = dev_state.get("last_reset_status", "NONE")
    pump_state = dev_state.get("pump_state", "UNKNOWN")
    valve_state = dev_state.get("valve_state", "UNKNOWN")
    uv_status = dev_state.get("uv_status", "UNKNOWN")
    local_interlock = dev_state.get("local_interlock_state", "UNKNOWN")
    last_received = dev_state.get("last_received_at")
    fault_codes = dev_state.get("fault_codes", [])

    is_test = is_test_device(device_id)

    # Command status evaluation
    cmd_display_status = "NO ACTIVE COMMAND"
    cmd_details = None

    if latest_cmd:
        raw_status = latest_cmd["status"]
        cmd_type = latest_cmd.get("command_type", "STOP")
        expires_at_str = latest_cmd.get("expires_at")

        # Check if expired while still REQUESTED or QUEUED
        is_expired = False
        if expires_at_str and raw_status in ("QUEUED", "REQUESTED"):
            try:
                exp_dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > exp_dt:
                    is_expired = True
                    update_command_status(
                        latest_cmd["command_id"],
                        "ACK_TIMEOUT",
                        failure_reason=f"{cmd_type} command expired without acknowledgement",
                        db_path=db_path
                    )
                    raw_status = "ACK_TIMEOUT"
            except Exception:
                pass

        if cmd_type == "RESET":
            if raw_status == "QUEUED":
                cmd_display_status = CommandState.RESET_QUEUED
            elif raw_status == "REQUESTED":
                cmd_display_status = CommandState.RESET_REQUESTED
            else:
                cmd_display_status = raw_status
        elif cmd_type == "START":
            if raw_status == "QUEUED":
                cmd_display_status = CommandState.START_QUEUED
            elif raw_status == "REQUESTED":
                cmd_display_status = CommandState.START_REQUESTED
            else:
                cmd_display_status = raw_status
        else:  # STOP
            if raw_status == "QUEUED":
                cmd_display_status = CommandState.STOP_QUEUED
            elif raw_status == "REQUESTED":
                cmd_display_status = CommandState.STOP_REQUESTED
            elif raw_status == "ACKNOWLEDGED":
                if pump_state == "OFF":
                    cmd_display_status = CommandState.DEVICE_REPORTS_STOPPED
                else:
                    cmd_display_status = CommandState.ACKNOWLEDGED
            elif raw_status == "DEVICE_REPORTS_STOPPED":
                cmd_display_status = CommandState.DEVICE_REPORTS_STOPPED
            else:
                cmd_display_status = raw_status

        cmd_details = {
            "command_id": latest_cmd["command_id"],
            "command_type": latest_cmd["command_type"],
            "origin": latest_cmd["origin"],
            "status": raw_status,
            "stop_generation": latest_cmd.get("stop_generation", 0),
            "created_at": latest_cmd["created_at"],
            "expires_at": latest_cmd.get("expires_at"),
            "acknowledged_at": latest_cmd.get("acknowledged_at"),
            "failure_reason": latest_cmd.get("failure_reason")
        }

    # Physical stop verification: pump MUST be reported OFF
    physical_stop_verified = (pump_state == "OFF")

    # Specific banner messages
    if manual_stop_latched:
        if not is_test:
            reset_banner = "Resolve the fault and reset locally on the ESP32. Waiting for device confirmation."
        else:
            reset_banner = "Test device stopped. Awaiting simulator reset."
    else:
        if last_reset_status == "LOCAL_RESET_CONFIRMED":
            reset_banner = "Local reset confirmed — equipment stopped."
        elif last_reset_status == "RESET_COMPLETE":
            reset_banner = "Reset complete — stopped, ready for simulated start."
        else:
            reset_banner = "System normal — interlock operational."

    can_reset_sim = is_test and manual_stop_latched and len(fault_codes) == 0
    can_start_sim = (
        is_test and
        not manual_stop_latched and
        len(fault_codes) == 0 and
        local_interlock in ("NORMAL", "ARMED") and
        pump_state == "OFF"
    )

    return {
        "device_id": device_id,
        "is_test_device": is_test,
        "broker_connected": broker_connected,
        "manual_stop_latched": manual_stop_latched,
        "stop_reason": stop_reason,
        "stop_generation": stop_generation,
        "last_reset_status": last_reset_status,
        "reset_banner_message": reset_banner,
        "command_status": cmd_display_status,
        "command_details": cmd_details,
        "physical_stop_verified": physical_stop_verified,
        "can_reset_simulator": can_reset_sim,
        "can_start_simulator": can_start_sim,
        "reported_states": {
            "pump_state": pump_state,
            "valve_state": valve_state,
            "uv_status": uv_status,
            "local_interlock_state": local_interlock
        },
        "last_received_at": last_received
    }
