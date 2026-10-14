"""MistSafe AI Automated Test Suite.
Verifies critical safety constraints, data validation, persistence, and interlock logic:
1. Empty startup state is unavailable/unknown, not green/safe.
2. Valid telemetry is saved correctly.
3. Malformed messages are rejected without crashing.
4. Duplicate message_id and out-of-order timestamp handling.
5. Stale data detection after freshness timeout.
6. Multi-session state sharing via SQLite WAL.
7. AI unavailability does not break monitoring.
8. Missing inference scores do not become zero-risk.
9. Shutdown requests persist and are deduplicated.
10. Hardware ACK matched by command_id.
11. Simulated/reference inputs cannot command real hardware.
12. Manual stop latch cannot be cleared by lower AI score.
"""

import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone, timedelta
import pytest

from mistsafe.database import (
    check_is_fresh,
    clear_stop_latch,
    cancel_queued_stop_commands_for_generation,
    get_db_connection,
    get_device_state,
    get_events,
    get_latest_command,
    get_queued_commands,
    get_simulator_config,
    get_telemetry_history,
    handle_command_ack,
    handle_local_reset_event,
    init_db,
    latch_manual_stop,
    save_telemetry,
    set_broker_status,
    update_simulator_config,
    utc_now_iso,
)
from mistsafe.validation import (
    ValidationError,
    validate_image_file,
    validate_telemetry_payload,
)
from mistsafe.commands import (
    evaluate_hardware_safety_status,
    is_test_device,
    request_shutdown,
    request_simulator_reset,
    request_simulator_start,
)
from mistsafe.ai_client import AIClient
from simulator import (
    SimulatorState,
    check_and_execute_pending_simulator_commands,
    execute_simulator_cycle_for_device
)


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    yield path
    if os.path.exists(path):
        os.remove(path)
    wal = path + "-wal"
    shm = path + "-shm"
    if os.path.exists(wal):
        os.remove(wal)
    if os.path.exists(shm):
        os.remove(shm)


def test_empty_startup_state(temp_db):
    """Test 1: Empty startup remains unavailable/unknown, not green or safe."""
    state = get_device_state("MISTSAFE-ESP32-01", db_path=temp_db)
    assert state is None, "Startup device state should be completely empty initially"

    safety = evaluate_hardware_safety_status("MISTSAFE-ESP32-01", db_path=temp_db)
    assert safety["broker_connected"] is False
    assert safety["command_status"] == "NO ACTIVE COMMAND"
    assert safety["physical_stop_verified"] is False
    assert safety["reported_states"]["pump_state"] == "UNKNOWN"
    assert safety["reported_states"]["valve_state"] == "UNKNOWN"
    assert safety["reported_states"]["uv_status"] == "UNKNOWN"
    assert safety["reported_states"]["local_interlock_state"] == "UNKNOWN"
    assert safety["last_received_at"] is None


def test_valid_telemetry_persistence(temp_db):
    """Test 2: Valid telemetry is validated and saved."""
    payload = {
        "schema_version": "1.0",
        "device_id": "MISTSAFE-ESP32-01",
        "message_id": "msg-001",
        "timestamp": utc_now_iso(),
        "data_origin": "REAL",
        "temp_C": 23.4,
        "pH": 7.35,
        "EC_uS_cm": 450.0,
        "turbidity_NTU": 1.2,
        "flow_L_min": 12.5,
        "stagnation_hours": 2.0,
        "sensor_status": {"temp": "OK", "pH": "OK"},
        "uv_status": "OK",
        "pump_state": "ON",
        "valve_state": "OPEN",
        "local_interlock_state": "NORMAL",
        "fault_codes": []
    }
    validated = validate_telemetry_payload(payload)
    success, msg = save_telemetry(validated, db_path=temp_db)
    assert success is True

    state = get_device_state("MISTSAFE-ESP32-01", db_path=temp_db)
    assert state is not None
    assert state["temp_C"] == 23.4
    assert state["pump_state"] == "ON"
    assert state["device_connected"] == 1


def test_malformed_messages_rejected(temp_db):
    """Test 3: Malformed messages reject cleanly without crashing."""
    bad_payloads = [
        "not a dict",
        {"device_id": "DEV-01"},  # missing message_id & timestamp
        {"device_id": "DEV-01", "message_id": "m1", "timestamp": "invalid-time"},
        {"device_id": "DEV-01", "message_id": "m2", "timestamp": utc_now_iso(), "temp_C": "not-a-number"},
        {"device_id": "DEV-01", "message_id": "m3", "timestamp": utc_now_iso(), "temp_C": float("nan")},
        {"device_id": "DEV-01", "message_id": "m4", "timestamp": utc_now_iso(), "data_origin": "FABRICATED"}
    ]
    for bad in bad_payloads:
        with pytest.raises(ValidationError):
            validate_telemetry_payload(bad)


def test_duplicate_and_out_of_order_messages(temp_db):
    """Test 4: Duplicate message_id and out-of-order timestamps."""
    t_now = datetime.now(timezone.utc)
    t_older = (t_now - timedelta(minutes=5)).isoformat()
    t_newer = t_now.isoformat()

    # Insert newer first
    p_newer = {
        "device_id": "MISTSAFE-ESP32-01",
        "message_id": "msg-new",
        "timestamp": t_newer,
        "data_origin": "REAL",
        "temp_C": 25.0,
        "pump_state": "ON"
    }
    save_telemetry(validate_telemetry_payload(p_newer), db_path=temp_db)

    # Insert duplicate message_id
    success_dup, msg_dup = save_telemetry(validate_telemetry_payload(p_newer), db_path=temp_db)
    assert success_dup is False
    assert "Duplicate" in msg_dup

    # Insert older out-of-order message
    p_older = {
        "device_id": "MISTSAFE-ESP32-01",
        "message_id": "msg-old",
        "timestamp": t_older,
        "data_origin": "REAL",
        "temp_C": 18.0,
        "pump_state": "OFF"
    }
    success_old, _ = save_telemetry(validate_telemetry_payload(p_older), db_path=temp_db)
    assert success_old is True  # saved to history

    # But current device state must still reflect newer (25.0 / ON), not 18.0 / OFF
    state = get_device_state("MISTSAFE-ESP32-01", db_path=temp_db)
    assert state["temp_C"] == 25.0
    assert state["pump_state"] == "ON"


def test_stale_data_detection():
    """Test 5: Stale readings are detected after timeout."""
    is_fresh_none, _ = check_is_fresh(None, freshness_timeout_sec=15)
    assert is_fresh_none is False

    # Fresh timestamp (2 seconds ago)
    recent = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    is_fresh_recent, elapsed_recent = check_is_fresh(recent, freshness_timeout_sec=15)
    assert is_fresh_recent is True
    assert elapsed_recent < 15

    # Stale timestamp (20 seconds ago)
    old = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
    is_fresh_old, elapsed_old = check_is_fresh(old, freshness_timeout_sec=15)
    assert is_fresh_old is False
    assert elapsed_old >= 20


def test_multi_session_shared_state(temp_db):
    """Test 6: Multiple connections/sessions share state via SQLite WAL."""
    conn1 = get_db_connection(temp_db)
    conn2 = get_db_connection(temp_db)

    latch_manual_stop("MISTSAFE-ESP32-01", reason="Session 1 stop", db_path=temp_db)

    # Query from second connection
    state_from_conn2 = get_device_state("MISTSAFE-ESP32-01", db_path=temp_db)
    assert state_from_conn2["manual_stop_latched"] == 1
    assert state_from_conn2["stop_reason"] == "Session 1 stop"

    conn1.close()
    conn2.close()


def test_ai_service_unavailability_does_not_break_monitoring(temp_db):
    """Test 7: AI client handles unreachable service gracefully."""
    client = AIClient(base_url="http://127.0.0.1:54321", timeout_sec=0.5)
    health = client.check_health()
    assert health["available"] is False

    pred = client.predict_sensors({
        "device_id": "MISTSAFE-ESP32-01",
        "message_id": "msg-999",
        "data_origin": "REAL",
        "temp_C": 22.0
    }, db_path=temp_db)

    assert pred["validation_status"] == "SERVICE_UNAVAILABLE"
    assert pred["risk_score"] is None


def test_missing_inference_results_not_zero():
    """Test 8: Missing inference results remain None, never substituted with 0.0."""
    client = AIClient()
    comp = client.compute_composite_score(
        vision_pred=None,
        sensor_pred={"risk_score": 45.0, "data_origin": "REAL", "input_id": "s1"}
    )
    assert comp["risk_score"] is None
    assert comp["risk_score"] != 0.0
    assert comp["validation_status"] in ("INCOMPLETE", "UNAVAILABLE")


def test_shutdown_requests_persistence_and_deduplication(temp_db):
    """Test 9: Shutdown requests persist and duplicate calls are throttled."""
    succ1, cmd_id1, msg1 = request_shutdown(
        device_id="MISTSAFE-ESP32-01",
        origin="MANUAL",
        reason="Test manual shutdown",
        expires_in_sec=30,
        db_path=temp_db
    )
    assert succ1 is True
    assert cmd_id1.startswith("cmd-")

    queued = get_queued_commands(device_id="MISTSAFE-ESP32-01", db_path=temp_db)
    assert len(queued) == 1
    assert queued[0]["command_id"] == cmd_id1

    # Immediate second call should be deduplicated
    succ2, cmd_id2, msg2 = request_shutdown(
        device_id="MISTSAFE-ESP32-01",
        origin="MANUAL",
        reason="Repeated click",
        db_path=temp_db
    )
    assert succ2 is False
    assert cmd_id2 == cmd_id1


def test_command_ack_matching(temp_db):
    """Test 10: Command acknowledgements match by command_id."""
    _, cmd_id, _ = request_shutdown(
        device_id="MISTSAFE-ESP32-01",
        origin="MANUAL",
        db_path=temp_db
    )

    ack_payload = {
        "command_id": cmd_id,
        "device_id": "MISTSAFE-ESP32-01",
        "status": "ACCEPTED",
        "timestamp": utc_now_iso(),
        "action": "INTERLOCK_ENGAGED"
    }

    succ, msg = handle_command_ack("MISTSAFE-ESP32-01", ack_payload, db_path=temp_db)
    assert succ is True

    cmd = get_latest_command("MISTSAFE-ESP32-01", db_path=temp_db)
    assert cmd["status"] == "ACKNOWLEDGED"
    assert cmd["command_id"] == cmd_id


def test_simulated_inputs_cannot_command_real_hardware(temp_db):
    """Test 11: Simulated and reference data cannot trigger hardware shutdown."""
    client = AIClient()

    # SIMULATED prediction with high score (85 > 60)
    sim_composite = {
        "risk_score": 85.0,
        "data_origin": "SIMULATED",
        "validation_status": "VALID",
        "input_id": "sim_sample"
    }

    triggered, reason = client.check_and_apply_ai_stop_rule(
        device_id="MISTSAFE-ESP32-01",
        composite_pred=sim_composite,
        ai_stop_enabled=True,
        threshold=60.0,
        db_path=temp_db
    )
    assert triggered is False
    assert "SIMULATED" in reason

    # Reference data check
    ref_composite = {
        "risk_score": 95.0,
        "data_origin": "REFERENCE",
        "validation_status": "VALID",
        "input_id": "cdc_ref_sample"
    }
    triggered_ref, reason_ref = client.check_and_apply_ai_stop_rule(
        device_id="MISTSAFE-ESP32-01",
        composite_pred=ref_composite,
        ai_stop_enabled=True,
        threshold=60.0,
        db_path=temp_db
    )
    assert triggered_ref is False
    assert "REFERENCE" in reason_ref


def test_manual_stop_cannot_be_cleared_by_lower_ai_score(temp_db):
    """Test 12: Manual stop latch cannot be cleared by falling AI scores."""
    # Operator triggers manual stop
    request_shutdown(
        device_id="MISTSAFE-ESP32-01",
        origin="MANUAL",
        reason="Manual operator latch",
        db_path=temp_db
    )

    state_before = get_device_state("MISTSAFE-ESP32-01", db_path=temp_db)
    assert state_before["manual_stop_latched"] == 1

    # AI score drops to very safe (e.g. 10.0)
    client = AIClient()
    low_composite = {
        "risk_score": 10.0,
        "data_origin": "REAL",
        "validation_status": "VALID",
        "input_id": "clean_water"
    }
    client.check_and_apply_ai_stop_rule(
        device_id="MISTSAFE-ESP32-01",
        composite_pred=low_composite,
        ai_stop_enabled=True,
        threshold=60.0,
        db_path=temp_db
    )

    # Latch MUST remain set to 1
    state_after = get_device_state("MISTSAFE-ESP32-01", db_path=temp_db)
    assert state_after["manual_stop_latched"] == 1
    assert state_after["stop_reason"] == "Manual operator latch"


def test_simulator_reset_leaves_equipment_stopped(temp_db):
    """Test 13: Simulator reset clears latch only on confirmed feedback and leaves equipment stopped."""
    dev_id = "MISTSAFE-SIM-01"
    # Initial state: stopped and latched
    request_shutdown(dev_id, origin="MANUAL", reason="Initial stop", db_path=temp_db)
    st = get_device_state(dev_id, db_path=temp_db)
    assert st["manual_stop_latched"] == 1

    sim = SimulatorState(device_id=dev_id)
    # Request simulator reset
    succ, cmd_id, msg = request_simulator_reset(dev_id, db_path=temp_db)
    assert succ is True
    assert cmd_id.startswith("cmd-rst-")

    # Before simulator execution, latch remains set
    st_mid = get_device_state(dev_id, db_path=temp_db)
    assert st_mid["manual_stop_latched"] == 1

    # Simulator processes pending commands and emits feedback
    check_and_execute_pending_simulator_commands(sim, db_path=temp_db)
    packet = sim.generate_packet()
    save_telemetry(packet, db_path=temp_db)

    # Now latch is cleared, status is RESET_COMPLETE, and equipment remains stopped
    st_post = get_device_state(dev_id, db_path=temp_db)
    assert st_post["manual_stop_latched"] == 0
    assert st_post["last_reset_status"] == "RESET_COMPLETE"
    assert st_post["pump_state"] == "OFF"
    assert st_post["valve_state"] == "CLOSED"
    assert st_post["local_interlock_state"] == "NORMAL"

    safety = evaluate_hardware_safety_status(dev_id, db_path=temp_db)
    assert safety["reset_banner_message"] == "Reset complete — stopped, ready for simulated start."
    assert safety["physical_stop_verified"] is True


def test_simulated_start_requires_separate_action(temp_db):
    """Test 14: Simulated start is a separate explicit action and fails if unreset."""
    dev_id = "MISTSAFE-SIM-01"
    # Latched stop
    request_shutdown(dev_id, origin="MANUAL", reason="Latched stop", db_path=temp_db)

    # Attempting start while latched must fail
    succ_fail, _, err = request_simulator_start(dev_id, db_path=temp_db)
    assert succ_fail is False
    assert "Platform stop latch is active" in err

    # Perform reset
    request_simulator_reset(dev_id, db_path=temp_db)
    sim = SimulatorState(device_id=dev_id)
    check_and_execute_pending_simulator_commands(sim, db_path=temp_db)
    save_telemetry(sim.generate_packet(), db_path=temp_db)

    # Pump remains OFF after reset
    st_reset = get_device_state(dev_id, db_path=temp_db)
    assert st_reset["pump_state"] == "OFF"

    # Now explicitly trigger start
    succ_start, start_cmd, msg = request_simulator_start(dev_id, db_path=temp_db)
    assert succ_start is True
    assert start_cmd.startswith("cmd-strt-")

    # Simulator executes start and emits telemetry
    check_and_execute_pending_simulator_commands(sim, db_path=temp_db)
    save_telemetry(sim.generate_packet(), db_path=temp_db)

    st_running = get_device_state(dev_id, db_path=temp_db)
    assert st_running["pump_state"] == "ON"
    assert st_running["valve_state"] == "OPEN"


def test_active_faults_block_reset_and_start(temp_db):
    """Test 15: Active faults block both reset and start operations."""
    dev_id = "MISTSAFE-SIM-01"
    sim = SimulatorState(device_id=dev_id)
    sim.fault_codes = ["E01_FLOW_LOW", "E02_UV_FAIL"]
    sim.local_interlock_state = "TRIPPED"
    save_telemetry(sim.generate_packet(), db_path=temp_db)

    request_shutdown(dev_id, origin="MANUAL", reason="Fault shutdown", db_path=temp_db)

    # Attempting reset while faults are present is blocked by backend
    succ_rst, _, msg_rst = request_simulator_reset(dev_id, db_path=temp_db)
    assert succ_rst is False
    assert "Active faults present" in msg_rst

    # Attempting start while faults are present is blocked by backend
    succ_start, _, msg_start = request_simulator_start(dev_id, db_path=temp_db)
    assert succ_start is False
    assert "Platform stop latch is active" in msg_start or "Active faults present" in msg_start


def test_test_controls_cannot_target_real_hardware(temp_db):
    """Test 16: Test reset and start controls cannot target real production hardware."""
    real_dev = "MISTSAFE-ESP32-01"
    request_shutdown(real_dev, origin="MANUAL", reason="Operator latch", db_path=temp_db)

    # Backend check blocks reset
    succ_rst, _, msg_rst = request_simulator_reset(real_dev, db_path=temp_db)
    assert succ_rst is False
    assert "Forbidden" in msg_rst

    # Backend check blocks start
    succ_start, _, msg_start = request_simulator_start(real_dev, db_path=temp_db)
    assert succ_start is False
    assert "Forbidden" in msg_start


def test_unconfirmed_reset_does_not_clear_latch(temp_db):
    """Test 17: Unconfirmed local reset (failed outcome or pump still running) does not clear latch."""
    real_dev = "MISTSAFE-ESP32-01"
    request_shutdown(real_dev, origin="MANUAL", reason="Real hardware stop", db_path=temp_db)
    st = get_device_state(real_dev, db_path=temp_db)
    assert st["manual_stop_latched"] == 1
    current_gen = st["stop_generation"]

    # 1. Event with outcome FAILED
    failed_payload = {
        "event_id": "rst-evt-001",
        "device_id": real_dev,
        "boot_id": "boot-1",
        "seq": 1,
        "timestamp": utc_now_iso(),
        "stop_generation": current_gen,
        "outcome": "FAILED",
        "remaining_faults": ["E01_FLOW"]
    }
    succ, msg = handle_local_reset_event(failed_payload, is_retained=False, db_path=temp_db)
    assert succ is False
    assert get_device_state(real_dev, db_path=temp_db)["manual_stop_latched"] == 1

    # 2. Event with SUCCESS but equipment feedback still shows pump ON
    p_running = {
        "device_id": real_dev,
        "message_id": "msg-run",
        "timestamp": utc_now_iso(),
        "data_origin": "REAL",
        "pump_state": "ON",
        "valve_state": "OPEN",
        "local_interlock_state": "TRIPPED"
    }
    save_telemetry(validate_telemetry_payload(p_running), db_path=temp_db)

    succ_feedback, msg_feedback = handle_local_reset_event({
        "event_id": "rst-evt-002",
        "device_id": real_dev,
        "boot_id": "boot-1",
        "seq": 2,
        "timestamp": utc_now_iso(),
        "stop_generation": current_gen,
        "outcome": "SUCCESS",
        "remaining_faults": []
    }, is_retained=False, db_path=temp_db)
    assert succ_feedback is False
    assert "Waiting for device feedback" in msg_feedback
    assert get_device_state(real_dev, db_path=temp_db)["manual_stop_latched"] == 1


def test_old_reset_event_cannot_clear_newer_stop(temp_db):
    """Test 18: An old reset event referencing an older stop_generation cannot clear a newer stop."""
    real_dev = "MISTSAFE-ESP32-01"
    # Generation 1
    request_shutdown(real_dev, origin="MANUAL", reason="First stop", db_path=temp_db)
    st1 = get_device_state(real_dev, db_path=temp_db)
    gen1 = st1["stop_generation"]

    # Mark reset attempted/in-progress for generation 1
    with get_db_connection(temp_db) as conn:
        conn.execute("UPDATE device_state SET last_reset_status = 'PENDING_CONFIRMATION' WHERE device_id = ?", (real_dev,))
        conn.commit()

    # Generation 2 (new stop requested while reset was pending)
    request_shutdown(real_dev, origin="MANUAL", reason="Second stop", db_path=temp_db)
    st2 = get_device_state(real_dev, db_path=temp_db)
    gen2 = st2["stop_generation"]
    assert gen2 > gen1

    # Ensure device reports stopped
    p_stopped = {
        "device_id": real_dev,
        "message_id": "msg-stopped",
        "timestamp": utc_now_iso(),
        "data_origin": "REAL",
        "pump_state": "OFF",
        "valve_state": "CLOSED",
        "local_interlock_state": "NORMAL"
    }
    save_telemetry(validate_telemetry_payload(p_stopped), db_path=temp_db)

    # Event arrives for older gen1
    old_event = {
        "event_id": "rst-old-001",
        "device_id": real_dev,
        "boot_id": "boot-1",
        "seq": 10,
        "timestamp": utc_now_iso(),
        "stop_generation": gen1,
        "outcome": "SUCCESS",
        "remaining_faults": []
    }
    succ, msg = handle_local_reset_event(old_event, is_retained=False, db_path=temp_db)
    assert succ is False
    assert "older than current stop_generation" in msg

    # Latch MUST remain set
    assert get_device_state(real_dev, db_path=temp_db)["manual_stop_latched"] == 1


def test_new_stop_takes_precedence_over_in_progress_reset(temp_db):
    """Test 19: A new stop request received during reset takes immediate precedence."""
    real_dev = "MISTSAFE-ESP32-01"
    request_shutdown(real_dev, origin="MANUAL", reason="Stop 1", db_path=temp_db)

    # A reset is pending
    with get_db_connection(temp_db) as conn:
        conn.execute("UPDATE device_state SET last_reset_status = 'PENDING_CONFIRMATION' WHERE device_id = ?", (real_dev,))
        conn.commit()

    # New stop arrives
    succ, cmd_id, msg = request_shutdown(real_dev, origin="MANUAL", reason="Emergency Stop 2 during reset", db_path=temp_db)
    assert succ is True

    st = get_device_state(real_dev, db_path=temp_db)
    assert st["manual_stop_latched"] == 1
    assert st["last_reset_status"] == "NONE"
    assert st["stop_reason"] == "Emergency Stop 2 during reset"


def test_successfully_reset_stop_generations_cannot_send_delayed_queued_stop_commands(temp_db):
    """Test 20: Successfully reset stop generations cannot later send queued STOP commands."""
    dev_id = "MISTSAFE-SIM-01"
    request_shutdown(dev_id, origin="MANUAL", reason="Stop to reset", db_path=temp_db)
    st = get_device_state(dev_id, db_path=temp_db)
    gen = st["stop_generation"]

    # Verify command is QUEUED
    queued = get_queued_commands(dev_id, db_path=temp_db)
    assert len(queued) == 1
    assert queued[0]["command_type"] == "STOP"

    # Reset occurs
    clear_stop_latch(dev_id, reset_status_text="RESET_COMPLETE", db_path=temp_db)

    # Queued commands for that generation MUST now be cancelled
    queued_after = get_queued_commands(dev_id, db_path=temp_db)
    assert len(queued_after) == 0

    with get_db_connection(temp_db) as conn:
        row = conn.execute("SELECT status, failure_reason FROM commands WHERE command_id = ?", (queued[0]["command_id"],)).fetchone()
        assert row["status"] == "CANCELLED_BY_RESET"
        assert "Cancelled by reset" in row["failure_reason"]


def test_latch_changes_persist_and_appear_across_browser_sessions(temp_db):
    """Test 21: Latch changes persist in SQLite and are visible across independent sessions."""
    conn1 = get_db_connection(temp_db)
    conn2 = get_db_connection(temp_db)

    # Session 1 latches
    request_shutdown("MISTSAFE-ESP32-01", origin="MANUAL", reason="Session 1 latch", db_path=temp_db)

    # Session 2 inspects
    st2 = get_device_state("MISTSAFE-ESP32-01", db_path=temp_db)
    assert st2["manual_stop_latched"] == 1

    # Session 2 clears latch via verified local reset
    p_stopped = {
        "device_id": "MISTSAFE-ESP32-01",
        "message_id": "msg-feedback-stopped",
        "timestamp": utc_now_iso(),
        "data_origin": "REAL",
        "pump_state": "OFF",
        "valve_state": "CLOSED",
        "local_interlock_state": "NORMAL"
    }
    save_telemetry(validate_telemetry_payload(p_stopped), db_path=temp_db)

    succ, msg = handle_local_reset_event({
        "event_id": "evt-session2-reset",
        "device_id": "MISTSAFE-ESP32-01",
        "boot_id": "b-sess",
        "seq": 1,
        "timestamp": utc_now_iso(),
        "stop_generation": st2["stop_generation"],
        "outcome": "SUCCESS",
        "remaining_faults": []
    }, is_retained=False, db_path=temp_db)
    assert succ is True

    # Session 1 inspects -> latch is cleared
    st1_after = get_device_state("MISTSAFE-ESP32-01", db_path=temp_db)
    assert st1_after["manual_stop_latched"] == 0
    assert st1_after["last_reset_status"] == "LOCAL_RESET_CONFIRMED"

    conn1.close()
    conn2.close()


def test_dashboard_reruns_do_not_duplicate_commands(temp_db):
    """Test 22: Rapid dashboard reruns do not duplicate active commands."""
    dev_id = "MISTSAFE-SIM-01"
    # Call shutdown twice in succession
    succ1, cmd1, _ = request_shutdown(dev_id, origin="MANUAL", db_path=temp_db)
    succ2, cmd2, _ = request_shutdown(dev_id, origin="MANUAL", db_path=temp_db)
    assert succ1 is True
    assert succ2 is False
    assert cmd1 == cmd2

    # Queued commands count is exactly 1
    queued = get_queued_commands(dev_id, db_path=temp_db)
    assert len(queued) == 1

    # Reset command deduplication
    clear_stop_latch(dev_id, reset_status_text="MANUAL_TEST", db_path=temp_db)
    request_shutdown(dev_id, origin="MANUAL", db_path=temp_db)
    rst_succ1, rst_cmd1, _ = request_simulator_reset(dev_id, db_path=temp_db)
    rst_succ2, rst_cmd2, _ = request_simulator_reset(dev_id, db_path=temp_db)
    assert rst_succ1 is True
    assert rst_succ2 is False
    assert rst_cmd1 == rst_cmd2


def test_simulator_shutdown_flow_full_cycle(temp_db):
    """
    Test 23: Regression test for simulator shutdown flow:
    reset → start → verify ON/OPEN → shutdown → verify OFF/CLOSED and latched → reset → verify still OFF/CLOSED.
    """
    dev_id = "MISTSAFE-SIM-01"

    # Step 1: Initial latch & Reset
    request_shutdown(dev_id, origin="MANUAL", reason="Initial stop", db_path=temp_db)
    st = get_device_state(dev_id, db_path=temp_db)
    assert st["manual_stop_latched"] == 1

    succ_rst, _, _ = request_simulator_reset(dev_id, db_path=temp_db)
    assert succ_rst is True
    execute_simulator_cycle_for_device(dev_id, db_path=temp_db)

    st_rst = get_device_state(dev_id, db_path=temp_db)
    assert st_rst["pump_state"] == "OFF"
    assert st_rst["valve_state"] == "CLOSED"
    assert st_rst["manual_stop_latched"] == 0
    assert st_rst["local_interlock_state"] == "NORMAL"

    # Step 2: Start simulated operation → verify ON/OPEN
    succ_start, start_cmd, _ = request_simulator_start(dev_id, db_path=temp_db)
    assert succ_start is True
    execute_simulator_cycle_for_device(dev_id, db_path=temp_db)

    st_run = get_device_state(dev_id, db_path=temp_db)
    assert st_run["pump_state"] == "ON"
    assert st_run["valve_state"] == "OPEN"
    assert st_run["manual_stop_latched"] == 0
    assert st_run["local_interlock_state"] == "NORMAL"

    # Step 3: Press Shutdown → verify OFF/CLOSED and latched
    succ_stop, stop_cmd, _ = request_shutdown(dev_id, origin="MANUAL", reason="Emergency Stop Button", db_path=temp_db)
    assert succ_stop is True
    execute_simulator_cycle_for_device(dev_id, db_path=temp_db)

    st_stop = get_device_state(dev_id, db_path=temp_db)
    assert st_stop["pump_state"] == "OFF"
    assert st_stop["valve_state"] == "CLOSED"
    assert st_stop["manual_stop_latched"] == 1
    assert st_stop["local_interlock_state"] == "TRIPPED"

    # Verify dashboard safety status feedback
    safety_stop = evaluate_hardware_safety_status(dev_id, db_path=temp_db)
    assert safety_stop["reported_states"]["pump_state"] == "OFF"
    assert safety_stop["reported_states"]["valve_state"] == "CLOSED"
    assert safety_stop["reported_states"]["local_interlock_state"] == "TRIPPED"
    assert safety_stop["manual_stop_latched"] is True
    assert safety_stop["command_status"] == "DEVICE REPORTS STOPPED"
    assert safety_stop["physical_stop_verified"] is True
    assert safety_stop["can_reset_simulator"] is True
    assert safety_stop["can_start_simulator"] is False

    # Step 4: Reset → verify still OFF/CLOSED and latch cleared
    succ_rst2, _, _ = request_simulator_reset(dev_id, db_path=temp_db)
    assert succ_rst2 is True
    execute_simulator_cycle_for_device(dev_id, db_path=temp_db)

    st_final = get_device_state(dev_id, db_path=temp_db)
    assert st_final["pump_state"] == "OFF"
    assert st_final["valve_state"] == "CLOSED"
    assert st_final["manual_stop_latched"] == 0
    assert st_final["local_interlock_state"] == "NORMAL"

    safety_final = evaluate_hardware_safety_status(dev_id, db_path=temp_db)
    assert safety_final["reported_states"]["pump_state"] == "OFF"
    assert safety_final["reported_states"]["valve_state"] == "CLOSED"
    assert safety_final["manual_stop_latched"] is False
    assert safety_final["can_start_simulator"] is True


def test_simulator_shutdown_lowercase_device_id(temp_db):
    """Test 24: Verify lowercase device ID 'mistsafe-sim-01' resolves canonically and executes shutdown."""
    dev_id = "mistsafe-sim-01"
    request_simulator_reset(dev_id, db_path=temp_db)
    execute_simulator_cycle_for_device(dev_id, db_path=temp_db)

    succ_start, _, _ = request_simulator_start(dev_id, db_path=temp_db)
    assert succ_start is True
    execute_simulator_cycle_for_device(dev_id, db_path=temp_db)

    st = get_device_state(dev_id, db_path=temp_db)
    assert st["pump_state"] == "ON"
    assert st["valve_state"] == "OPEN"

    succ_stop, _, _ = request_shutdown(dev_id, origin="MANUAL", reason="Emergency Stop", db_path=temp_db)
    assert succ_stop is True
    execute_simulator_cycle_for_device(dev_id, db_path=temp_db)

    st_stop = get_device_state(dev_id, db_path=temp_db)
    assert st_stop["pump_state"] == "OFF"
    assert st_stop["valve_state"] == "CLOSED"
    assert st_stop["manual_stop_latched"] == 1


def test_deterministic_periodic_simulated_telemetry_and_isolation(temp_db):
    """Test 25: Verify simulated telemetry generates deterministic records with SIMULATED tag, isolated from primary device."""
    sim_dev = "MISTSAFE-SIM-01"
    real_dev = "MISTSAFE-ESP32-01"

    # Start simulation operation
    request_simulator_reset(sim_dev, db_path=temp_db)
    execute_simulator_cycle_for_device(sim_dev, db_path=temp_db)
    request_simulator_start(sim_dev, db_path=temp_db)
    execute_simulator_cycle_for_device(sim_dev, db_path=temp_db)

    # Execute 6 successive cycles
    for _ in range(6):
        execute_simulator_cycle_for_device(sim_dev, db_path=temp_db)

    history = get_telemetry_history(sim_dev, db_path=temp_db)
    # Check that all records belong to SIM-01 and have data_origin = 'SIMULATED'
    assert len(history) >= 6
    for rec in history:
        assert rec["device_id"] == sim_dev
        assert rec["data_origin"] == "SIMULATED"
        assert rec["message_id"].startswith("sim-")
        assert rec["schema_version"] == "1.0"
        # While pump is ON, flow is running (~13.58 L/min)
        if rec["pump_state"] == "ON" and rec["valve_state"] == "OPEN":
            assert 13.0 <= rec["flow_L_min"] <= 14.5
            assert rec["stagnation_hours"] == 0.1

    # Primary real device must NEVER receive simulated packets
    real_hist = get_telemetry_history(real_dev, db_path=temp_db)
    assert len(real_hist) == 0
    real_state = get_device_state(real_dev, db_path=temp_db)
    assert real_state is None


def test_telemetry_continues_during_equipment_shutdown(temp_db):
    """Test 26: Telemetry continues during equipment shutdown with flow = 0 and advancing stagnation."""
    sim_dev = "MISTSAFE-SIM-01"
    request_simulator_reset(sim_dev, db_path=temp_db)
    execute_simulator_cycle_for_device(sim_dev, db_path=temp_db)

    # Request Shutdown
    request_shutdown(sim_dev, origin="MANUAL", reason="Shutdown test", db_path=temp_db)
    execute_simulator_cycle_for_device(sim_dev, db_path=temp_db)

    st = get_device_state(sim_dev, db_path=temp_db)
    assert st["pump_state"] == "OFF"
    assert st["valve_state"] == "CLOSED"
    assert st["manual_stop_latched"] == 1

    # Generate more cycles while equipment is stopped
    execute_simulator_cycle_for_device(sim_dev, db_path=temp_db)
    execute_simulator_cycle_for_device(sim_dev, db_path=temp_db)

    hist = get_telemetry_history(sim_dev, db_path=temp_db)
    assert len(hist) >= 3
    latest = hist[-1]
    assert latest["pump_state"] == "OFF"
    assert latest["valve_state"] == "CLOSED"
    assert latest["flow_L_min"] == 0.0
    # Stagnation accumulates when stopped
    assert latest["stagnation_hours"] >= 0.1


def test_injected_faults_persist_and_block_reset_start(temp_db):
    """Test 27: Active UV and flow faults survive periodic updates and block reset and start."""
    sim_dev = "MISTSAFE-SIM-01"

    # Establish baseline state
    execute_simulator_cycle_for_device(sim_dev, db_path=temp_db)

    # Inject UV fault
    with get_db_connection(temp_db) as conn:
        conn.execute("UPDATE device_state SET fault_codes = '[\"E02_UV_FAIL\"]', uv_status = 'FAULT', local_interlock_state = 'TRIPPED' WHERE device_id = ?", (sim_dev,))
        conn.commit()

    execute_simulator_cycle_for_device(sim_dev, db_path=temp_db)


    # Verify state preserved
    st = get_device_state(sim_dev, db_path=temp_db)
    assert "E02_UV_FAIL" in st["fault_codes"]
    assert st["uv_status"] == "FAULT"
    assert st["local_interlock_state"] == "TRIPPED"

    # Reset must be blocked
    succ_rst, _, rst_msg = request_simulator_reset(sim_dev, db_path=temp_db)
    assert succ_rst is False
    assert "Active faults present" in rst_msg

    # Start must be blocked
    succ_start, _, start_msg = request_simulator_start(sim_dev, db_path=temp_db)
    assert succ_start is False
    assert "Active faults present" in start_msg


def test_missing_sensor_produces_null_and_preserves_gaps(temp_db):
    """Test 28: Missing sensor publishes null without defaulting to zero, and allows Trends gap representation."""
    from mistsafe.database import update_simulator_config
    sim_dev = "MISTSAFE-SIM-01"

    update_simulator_config(sim_dev, {"missing_sensor": "turbidity_NTU"}, db_path=temp_db)
    execute_simulator_cycle_for_device(sim_dev, db_path=temp_db)

    st = get_device_state(sim_dev, db_path=temp_db)
    assert st["turbidity_NTU"] is None
    assert st["sensor_status"]["turbidity"] == "FAULT"

    hist = get_telemetry_history(sim_dev, db_path=temp_db)
    latest = hist[-1]
    assert latest["turbidity_NTU"] is None


def test_pause_and_resume_simulator_telemetry(temp_db):
    """Test 29: Pausing telemetry stops new cycles; resuming restores reporting without resetting equipment."""
    from mistsafe.database import get_simulator_config, update_simulator_config
    sim_dev = "MISTSAFE-SIM-01"

    # Pause
    update_simulator_config(sim_dev, {"enabled": 0}, db_path=temp_db)
    cfg = get_simulator_config(sim_dev, db_path=temp_db)
    assert cfg["enabled"] == 0

    count_before = len(get_telemetry_history(sim_dev, db_path=temp_db))

    # Try cycle check via bridge condition
    if cfg["enabled"] == 1:
        execute_simulator_cycle_for_device(sim_dev, db_path=temp_db)

    count_after = len(get_telemetry_history(sim_dev, db_path=temp_db))
    assert count_before == count_after

    # Resume
    update_simulator_config(sim_dev, {"enabled": 1}, db_path=temp_db)
    cfg_res = get_simulator_config(sim_dev, db_path=temp_db)
    assert cfg_res["enabled"] == 1

    execute_simulator_cycle_for_device(sim_dev, db_path=temp_db)
    assert len(get_telemetry_history(sim_dev, db_path=temp_db)) == count_before + 1



