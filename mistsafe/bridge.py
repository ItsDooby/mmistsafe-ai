"""Bridge between Web Server and MistSafe Python platform.
Executes database queries and commands cleanly using the native Python modules.
"""

import json
import sys
import os

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mistsafe.database import (
    DEFAULT_DB_PATH,
    check_is_fresh,
    get_db_connection,
    get_device_state,
    get_device_calibration,
    set_device_calibration,
    get_events,
    get_latest_ai_prediction,
    get_reset_events,
    get_samples,
    get_telemetry_history,
    handle_local_reset_event,
    init_db,
    normalize_device_id,
    save_sample_record,
    save_telemetry,
    utc_now_iso,
)
from mistsafe.commands import (
    evaluate_hardware_safety_status,
    is_test_device,
    request_shutdown,
    request_simulator_reset,
    request_simulator_start,
)
from simulator import (
    SimulatorState,
    check_and_execute_pending_simulator_commands,
    execute_simulator_cycle_for_device
)
from mistsafe.ai_client import AIClient
from mistsafe.validation import ValidationError, validate_image_file


def run_bridge():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No action specified"}))
        return

    action = sys.argv[1]
    db_path = os.getenv("DATABASE_PATH", DEFAULT_DB_PATH)
    init_db(db_path)

    try:
        if action == "status":
            device_id = sys.argv[2] if len(sys.argv) > 2 else os.getenv("MISTSAFE_DEVICE_ID", "MISTSAFE-ESP32-01")
            freshness_sec = float(os.getenv("FRESHNESS_TIMEOUT_SEC", "15"))

            state = get_device_state(device_id, db_path=db_path)
            safety = evaluate_hardware_safety_status(device_id, db_path=db_path)

            last_rcv = state.get("last_received_at") if state else None
            is_fresh, elapsed = check_is_fresh(last_rcv, freshness_timeout_sec=freshness_sec)

            ai_client = AIClient()
            ai_health = ai_client.check_health()

            sensor_pred = get_latest_ai_prediction("SENSORS", db_path=db_path)
            vision_pred = get_latest_ai_prediction("IMAGE", db_path=db_path)
            comp_pred = get_latest_ai_prediction("COMPOSITE", db_path=db_path)

            result = {
                "device_id": device_id,
                "state": state,
                "calibration": get_device_calibration(device_id, db_path=db_path),
                "safety": safety,
                "is_fresh": is_fresh,
                "freshness_timeout_sec": freshness_sec,
                "elapsed_seconds": elapsed,
                "ai_health": ai_health,
                "predictions": {
                    "sensors": sensor_pred,
                    "vision": vision_pred,
                    "composite": comp_pred,
                }
            }
            print(json.dumps(result))

        elif action == "calibration":
            payload = json.loads(sys.argv[3])
            result = set_device_calibration(sys.argv[2], payload.get("empty_distance_cm"), payload.get("full_distance_cm"), db_path=db_path)
            print(json.dumps({"success": True, "calibration": result}))

        elif action == "water_level":
            from mistsafe.database import update_simulator_config
            device_id = normalize_device_id(sys.argv[2])
            if device_id != "MISTSAFE-SIM-01":
                raise ValueError("Water level test controls require MISTSAFE-SIM-01")
            mode = sys.argv[3]
            if mode not in {"0", "25", "50", "75", "100", "missing", "fault"}:
                raise ValueError("Invalid test level")
            update_simulator_config(device_id, {"water_level_mode": mode}, db_path=db_path)
            print(json.dumps({"success": True, "message": "Level selected; applies on next periodic telemetry update."}))

        elif action == "history":
            device_id = sys.argv[2] if len(sys.argv) > 2 else os.getenv("MISTSAFE_DEVICE_ID", "MISTSAFE-ESP32-01")
            hours = float(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] != "all" else None
            history = get_telemetry_history(device_id, hours=hours, limit=500, db_path=db_path)
            print(json.dumps(history))

        elif action == "events":
            device_id = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "all" else None
            severity = sys.argv[3] if len(sys.argv) > 3 else "ALL"
            events = get_events(device_id=device_id, severity=severity, limit=100, db_path=db_path)
            print(json.dumps(events))

        elif action == "reset_events":
            device_id = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "all" else None
            r_events = get_reset_events(device_id=device_id, limit=50, db_path=db_path)
            print(json.dumps(r_events))

        elif action == "samples":
            device_id = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "all" else None
            samples = get_samples(device_id=device_id, db_path=db_path)
            print(json.dumps(samples))

        elif action == "save_sample":
            raw_payload = sys.argv[2] if len(sys.argv) > 2 else "{}"
            try:
                data = json.loads(raw_payload)
                dev_norm = normalize_device_id(data.get("device_id", "MISTSAFE-ESP32-01"))
                save_sample_record(
                    sample_id=data["sample_id"],
                    device_id=dev_norm,
                    collection_time=data.get("collection_time", utc_now_iso()),
                    sampling_location=data.get("location", "Nozzle Manifold"),
                    imaging_method=data.get("method", "Brightfield 40x"),
                    data_origin=data.get("data_origin", "REAL"),
                    image_path=data["image_path"],
                    image_safe_name=data["image_safe_name"],
                    reference_lab_method=data.get("lab_method", ""),
                    reference_lab_result=data.get("lab_result", ""),
                    db_path=db_path
                )
                print(json.dumps({"success": True, "sample_id": data["sample_id"], "device_id": dev_norm}))
            except Exception as e:
                print(json.dumps({"success": False, "error": str(e)}))

        elif action == "shutdown":
            device_id = sys.argv[2] if len(sys.argv) > 2 else os.getenv("MISTSAFE_DEVICE_ID", "MISTSAFE-ESP32-01")
            reason = sys.argv[3] if len(sys.argv) > 3 else "Operator manual emergency stop requested"
            dev_norm = normalize_device_id(device_id)
            success, cmd_id, msg = request_shutdown(dev_norm, origin="MANUAL", reason=reason, db_path=db_path)
            if success and is_test_device(dev_norm):
                execute_simulator_cycle_for_device(dev_norm, db_path=db_path)
            print(json.dumps({"success": success, "command_id": cmd_id, "message": msg}))

        elif action == "predict":
            device_id = sys.argv[2] if len(sys.argv) > 2 else os.getenv("MISTSAFE_DEVICE_ID", "MISTSAFE-ESP32-01")
            dev_norm = normalize_device_id(device_id)
            state = get_device_state(dev_norm, db_path=db_path)
            client = AIClient()
            sensor_pred = client.predict_sensors(state or {}, db_path=db_path)

            samples = get_samples(device_id=dev_norm, db_path=db_path)
            vision_pred = None
            if samples:
                smp = samples[0]
                vision_pred = client.predict_image(
                    sample_id=smp["sample_id"],
                    image_path=smp["image_path"],
                    sample_metadata=smp,
                    db_path=db_path
                )

            comp_pred = client.compute_composite_score(
                vision_pred=vision_pred,
                sensor_pred=sensor_pred,
                vision_weight=0.5,
                sensor_weight=0.5,
                db_path=db_path
            )

            print(json.dumps({
                "sensor_pred": sensor_pred,
                "vision_pred": vision_pred,
                "comp_pred": comp_pred
            }))

        elif action == "reset_simulator":
            device_id = sys.argv[2] if len(sys.argv) > 2 else "MISTSAFE-SIM-01"
            dev_norm = normalize_device_id(device_id)
            success, cmd_id, msg = request_simulator_reset(dev_norm, db_path=db_path)
            if success and is_test_device(dev_norm):
                execute_simulator_cycle_for_device(dev_norm, db_path=db_path)
            print(json.dumps({"success": success, "command_id": cmd_id, "message": msg}))

        elif action == "start_simulator":
            device_id = sys.argv[2] if len(sys.argv) > 2 else "MISTSAFE-SIM-01"
            dev_norm = normalize_device_id(device_id)
            success, cmd_id, msg = request_simulator_start(dev_norm, db_path=db_path)
            if success and is_test_device(dev_norm):
                execute_simulator_cycle_for_device(dev_norm, db_path=db_path)
            print(json.dumps({"success": success, "command_id": cmd_id, "message": msg}))

        elif action == "local_reset":
            device_id = sys.argv[2] if len(sys.argv) > 2 else os.getenv("MISTSAFE_DEVICE_ID", "MISTSAFE-ESP32-01")
            payload_str = sys.argv[3] if len(sys.argv) > 3 else "{}"
            payload = json.loads(payload_str)
            if "device_id" not in payload:
                payload["device_id"] = device_id
            success, msg = handle_local_reset_event(payload, is_retained=False, db_path=db_path)
            print(json.dumps({"success": success, "message": msg}))

        elif action == "set_simulator_faults":
            device_id = sys.argv[2] if len(sys.argv) > 2 else "MISTSAFE-SIM-01"
            faults_csv = sys.argv[3] if len(sys.argv) > 3 else ""
            dev_norm = normalize_device_id(device_id)
            faults = [f.strip() for f in faults_csv.split(",") if f.strip()]
            with get_db_connection(db_path) as conn:
                uv_st = "FAULT" if "E02_UV_FAIL" in faults else "OK"
                conn.execute("""
                    UPDATE device_state
                    SET fault_codes = ?,
                        uv_status = ?,
                        pump_state = 'OFF',
                        valve_state = 'CLOSED',
                        local_interlock_state = 'TRIPPED'
                    WHERE device_id = ? OR UPPER(device_id) = UPPER(?)
                """, (json.dumps(faults), uv_st, dev_norm, dev_norm))
                conn.commit()
            if is_test_device(dev_norm):
                execute_simulator_cycle_for_device(dev_norm, db_path=db_path)
            print(json.dumps({"success": True, "faults": faults}))

        elif action == "inject_flow_loss":
            device_id = sys.argv[2] if len(sys.argv) > 2 else "MISTSAFE-SIM-01"
            dev_norm = normalize_device_id(device_id)
            from mistsafe.database import update_simulator_config
            update_simulator_config(dev_norm, {
                "flow_loss_injected": 1,
                "flow_loss_cycles": 0
            }, db_path=db_path)
            if is_test_device(dev_norm):
                execute_simulator_cycle_for_device(dev_norm, db_path=db_path)
            print(json.dumps({"success": True, "message": "Injected flow loss (flow forced to 0.0, will fault after delay)"}))

        elif action == "inject_missing_sensor":
            device_id = sys.argv[2] if len(sys.argv) > 2 else "MISTSAFE-SIM-01"
            sensor_name = sys.argv[3] if len(sys.argv) > 3 else "temp"
            dev_norm = normalize_device_id(device_id)
            from mistsafe.database import update_simulator_config
            update_simulator_config(dev_norm, {
                "missing_sensor": sensor_name
            }, db_path=db_path)
            if is_test_device(dev_norm):
                execute_simulator_cycle_for_device(dev_norm, db_path=db_path)
            print(json.dumps({"success": True, "sensor": sensor_name}))

        elif action == "inject_out_of_limit":
            device_id = sys.argv[2] if len(sys.argv) > 2 else "MISTSAFE-SIM-01"
            sensor_name = sys.argv[3] if len(sys.argv) > 3 else "turbidity"
            val = float(sys.argv[4]) if len(sys.argv) > 4 else 18.0
            dev_norm = normalize_device_id(device_id)
            from mistsafe.database import update_simulator_config
            update_simulator_config(dev_norm, {
                "out_of_limit_sensor": sensor_name,
                "out_of_limit_value": val
            }, db_path=db_path)
            if is_test_device(dev_norm):
                execute_simulator_cycle_for_device(dev_norm, db_path=db_path)
            print(json.dumps({"success": True, "sensor": sensor_name, "value": val}))

        elif action == "clear_simulator_faults":
            device_id = sys.argv[2] if len(sys.argv) > 2 else "MISTSAFE-SIM-01"
            dev_norm = normalize_device_id(device_id)
            from mistsafe.database import update_simulator_config
            update_simulator_config(dev_norm, {
                "missing_sensor": None,
                "out_of_limit_sensor": None,
                "out_of_limit_value": None,
                "flow_loss_injected": 0,
                "flow_loss_cycles": 0
            }, db_path=db_path)
            with get_db_connection(db_path) as conn:
                conn.execute("""
                    UPDATE device_state
                    SET fault_codes = '[]',
                        uv_status = 'OK'
                    WHERE device_id = ? OR UPPER(device_id) = UPPER(?)
                """, (dev_norm, dev_norm))
                conn.commit()
            print(json.dumps({"success": True, "message": "Faults cleared on test device. Stop latch & stopped state preserved."}))

        elif action == "simulator_telemetry_control":
            # pause or resume
            device_id = sys.argv[2] if len(sys.argv) > 2 else "MISTSAFE-SIM-01"
            mode = sys.argv[3] if len(sys.argv) > 3 else "resume"  # pause or resume
            dev_norm = normalize_device_id(device_id)
            from mistsafe.database import update_simulator_config
            enabled_val = 0 if mode.lower() == "pause" else 1
            update_simulator_config(dev_norm, {"enabled": enabled_val}, db_path=db_path)
            print(json.dumps({"success": True, "enabled": enabled_val, "mode": mode}))

        elif action == "get_simulator_config":
            device_id = sys.argv[2] if len(sys.argv) > 2 else "MISTSAFE-SIM-01"
            dev_norm = normalize_device_id(device_id)
            from mistsafe.database import get_simulator_config
            cfg = get_simulator_config(dev_norm, db_path=db_path)
            print(json.dumps(cfg))

        elif action == "simulator_tick":
            # Execute one periodic cycle if enabled
            device_id = sys.argv[2] if len(sys.argv) > 2 else "MISTSAFE-SIM-01"
            dev_norm = normalize_device_id(device_id)
            from mistsafe.database import get_simulator_config
            cfg = get_simulator_config(dev_norm, db_path=db_path)
            if cfg.get("enabled", 1) == 1 and is_test_device(dev_norm):
                execute_simulator_cycle_for_device(dev_norm, db_path=db_path)
                print(json.dumps({"success": True, "ticked": True, "device_id": dev_norm}))
            else:
                print(json.dumps({"success": True, "ticked": False, "reason": "Paused or not a test device"}))


        else:
            print(json.dumps({"error": f"Unknown action: {action}"}))

    except Exception as e:
        print(json.dumps({"error": str(e)}))


if __name__ == "__main__":
    run_bridge()
