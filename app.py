"""MistSafe AI: Live Water Monitoring & Interlock Dashboard.
Streamlit application for ESP32 water misting prototype safety supervision.

Architecture:
- Shared state stored strictly in SQLite with WAL mode (multi-session concurrency).
- Session state strictly limited to UI preferences.
- Default startup mode is LIVE with no fabricated data.
- Distinct broker and device connectivity reporting.
- Prominent hardware STOP request with latching and verification.
"""

import io
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd
from PIL import Image
import streamlit as st

from mistsafe.ai_client import AIClient
from mistsafe.commands import (
    evaluate_hardware_safety_status,
    is_test_device,
    request_shutdown,
    request_simulator_reset,
    request_simulator_start
)
from mistsafe.database import (
    DEFAULT_DB_PATH,
    check_is_fresh,
    get_db_connection,
    get_device_state,
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
    utc_now_iso
)
from simulator import (
    SimulatorState,
    check_and_execute_pending_simulator_commands,
    execute_simulator_cycle_for_device
)
from mistsafe.validation import ValidationError, validate_image_file

# Streamlit Page Config
st.set_page_config(
    page_title="MistSafe AI: Live Water Monitoring & Interlock",
    page_icon="💧",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Environment and configuration
DEVICE_ID = os.getenv("MISTSAFE_DEVICE_ID", "MISTSAFE-ESP32-01")
DB_PATH = os.getenv("DATABASE_PATH", DEFAULT_DB_PATH)
FRESHNESS_TIMEOUT_SEC = float(os.getenv("FRESHNESS_TIMEOUT_SEC", "15"))
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://127.0.0.1:8008")
AI_STOP_THRESHOLD = float(os.getenv("AI_STOP_THRESHOLD", "60.0"))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")

os.makedirs(UPLOAD_DIR, exist_ok=True)
init_db(DB_PATH)

# Initialize AI Client
ai_client = AIClient(base_url=AI_SERVICE_URL)

# Initialize UI session state (Strictly UI preferences only!)
if "ui_selected_view" not in st.session_state:
    st.session_state.ui_selected_view = "Overview"
if "ui_auto_refresh" not in st.session_state:
    st.session_state.ui_auto_refresh = True
if "ui_refresh_interval" not in st.session_state:
    st.session_state.ui_refresh_interval = 5
if "ui_composite_enabled" not in st.session_state:
    st.session_state.ui_composite_enabled = os.getenv("COMPOSITE_SCORE_ENABLED", "false").lower() in ("true", "1")
if "ui_vision_weight" not in st.session_state:
    st.session_state.ui_vision_weight = float(os.getenv("COMPOSITE_VISION_WEIGHT", "0.5"))
if "ui_sensor_weight" not in st.session_state:
    st.session_state.ui_sensor_weight = float(os.getenv("COMPOSITE_SENSOR_WEIGHT", "0.5"))
if "ui_ai_stop_enabled" not in st.session_state:
    st.session_state.ui_ai_stop_enabled = os.getenv("AI_STOP_ENABLED", "false").lower() in ("true", "1")
if "ui_ai_stop_threshold" not in st.session_state:
    st.session_state.ui_ai_stop_threshold = AI_STOP_THRESHOLD


# Helper: Format numeric readings without defaulting to 0
def format_reading(val: Optional[float], unit: str, precision: int = 1) -> str:
    if val is None:
        return "—"
    return f"{round(val, precision):.{precision}f} {unit}".strip()


# Sidebar Navigation and System Status
with st.sidebar:
    st.title("💧 MistSafe AI")
    st.caption("ESP32 Water Misting Prototype Platform")
    st.divider()

    st.subheader("Navigation")
    views = ["Overview", "Trends", "Samples & Microscopy", "AI Analysis", "Events & Controls"]
    selected_view = st.radio(
        "Select View",
        views,
        index=views.index(st.session_state.ui_selected_view),
        label_visibility="collapsed"
    )
    st.session_state.ui_selected_view = selected_view

    st.divider()
    st.subheader("Active Device")
    active_device = st.text_input("Device ID", value=DEVICE_ID)

    # Health & Connectivity Snapshot
    st.divider()
    st.subheader("Subsystem Status")

    # Fetch live state from SQLite
    dev_state = get_device_state(active_device, db_path=DB_PATH)
    safety_summary = evaluate_hardware_safety_status(active_device, db_path=DB_PATH)

    broker_conn = safety_summary["broker_connected"]
    last_rcv = safety_summary["last_received_at"]
    is_fresh, elapsed = check_is_fresh(last_rcv, freshness_timeout_sec=FRESHNESS_TIMEOUT_SEC)

    # Separate Broker vs Device reporting
    st.write("**Broker Connection:**")
    if broker_conn:
        st.success("🟢 Broker Online (MQTT)", icon="✅")
    else:
        st.warning("⚪ Broker Offline / Unreachable", icon="⚠️")

    st.write("**Device Liveness:**")
    if dev_state is None:
        st.info("⚪ Waiting for telemetry...", icon="⏳")
    elif is_fresh:
        st.success(f"🟢 Active ({int(elapsed)}s ago)", icon="📡")
    else:
        st.error(f"🔴 Stale ({int(elapsed) if elapsed else '?'}s ago)", icon="⚠️")

    # Local AI Service Health
    st.write("**Local AI Service:**")
    ai_health = ai_client.check_health()
    if ai_health["available"]:
        st.success(f"🟢 Connected ({ai_health.get('version', 'OK')})", icon="🧠")
    else:
        st.warning("⚪ AI Service Unavailable", icon="🚫")

    st.divider()
    st.subheader("Auto Refresh")
    st.session_state.ui_auto_refresh = st.checkbox("Enable Live Refresh", value=st.session_state.ui_auto_refresh)
    if st.session_state.ui_auto_refresh:
        st.session_state.ui_refresh_interval = st.slider("Interval (sec)", 2, 30, st.session_state.ui_refresh_interval)


# Top Header
st.markdown("## MistSafe AI: Live Water Monitoring & Interlock")
st.caption(f"Monitored Node: **{active_device}** | Database: SQLite WAL Mode | Mode: **LIVE** (No Mock Data Fabricated)")

# 1. Four Independent Safety State Cards (Never combined, no fake green button)
st.markdown("#### Safety State Supervision (Independent Channels)")
st.caption("Safety states are reported independently. A reset clears an eligible stop latch; it does NOT start equipment.")

st_col1, st_col2, st_col3, st_col4 = st.columns(4)

with st_col1:
    st.write("**1. Platform Stop Latch**")
    if safety_summary["manual_stop_latched"]:
        st.error(f"🛑 LATCHED (Gen {safety_summary.get('stop_generation', 1)})")
        st.caption(f"Reason: {safety_summary.get('stop_reason', 'Manual Operator Request')}")
    else:
        st.success("🟢 UNLATCHED")
        st.caption("No platform stop latch active")

with st_col2:
    st.write("**2. ESP32 Hardware Interlock**")
    interlock_val = safety_summary["reported_states"]["local_interlock_state"]
    if interlock_val in ("NORMAL", "ARMED"):
        st.success(f"🛡️ {interlock_val}")
    elif interlock_val == "TRIPPED":
        st.error("🚨 TRIPPED")
    else:
        st.info(f"❓ {interlock_val}")
    st.caption("Physical hardware gate feedback")

with st_col3:
    st.write("**3. Command Progress**")
    cmd_stat = safety_summary["command_status"]
    if "STOP" in cmd_stat or "FAILED" in cmd_stat or "TIMEOUT" in cmd_stat:
        st.error(f"📡 {cmd_stat}")
    elif "RESET" in cmd_stat or "START" in cmd_stat:
        st.warning(f"📡 {cmd_stat}")
    else:
        st.info(f"📡 {cmd_stat}")
    if safety_summary.get("command_details"):
        st.caption(f"ID: `{safety_summary['command_details']['command_id']}`")
    else:
        st.caption("No active queued commands")

with st_col4:
    st.write("**4. Reported Equipment States**")
    pump_feedback = safety_summary["reported_states"]["pump_state"]
    valve_feedback = safety_summary["reported_states"]["valve_state"]
    st.write(f"Pump: **{pump_feedback}** | Valve: **{valve_feedback}**")
    if pump_feedback == "OFF" and valve_feedback == "CLOSED":
        st.info("🔒 Stopped & Isolated")
    elif pump_feedback == "ON":
        st.warning("💧 Misting Running")
    else:
        st.caption("Reported via telemetry readback")

st.divider()

# Reset & Operational State Banner
banner_msg = safety_summary.get("reset_banner_message", "")
is_test = is_test_device(active_device)

if safety_summary["manual_stop_latched"]:
    if not is_test:
        st.error("🛑 **Resolve the fault and reset locally on the ESP32. Waiting for device confirmation.**")
        st.caption("Remote reset and remote start are disabled for real hardware. Physical push-button reset on the ESP32 and fresh telemetry are required.")
    else:
        st.error(f"🛑 **{banner_msg}**")
else:
    if safety_summary.get("last_reset_status") == "LOCAL_RESET_CONFIRMED" or "Local reset confirmed" in banner_msg:
        st.success("✅ **Local reset confirmed — equipment stopped.**")
        st.caption("Interlock restored. Physical pump and valve remain stopped. Hardware will NOT automatically restart.")
    elif safety_summary.get("last_reset_status") == "RESET_COMPLETE" or "Reset complete" in banner_msg:
        st.success("✅ **Reset complete — stopped, ready for simulated start.**")
    else:
        st.info(f"ℹ️ {banner_msg}")

# Hardware Action Controls
ctl_btn1, ctl_btn2, ctl_btn3 = st.columns([1.5, 1.5, 2])

with ctl_btn1:
    if st.button("🚨 SHUTDOWN REQUEST", type="primary", use_container_width=True):
        dev_norm = normalize_device_id(active_device)
        success, cmd_id, msg = request_shutdown(
            device_id=dev_norm,
            origin="MANUAL",
            reason="Operator triggered manual emergency stop from dashboard",
            db_path=DB_PATH
        )
        if success and is_test:
            execute_simulator_cycle_for_device(dev_norm, db_path=DB_PATH)
        if success:
            st.toast(f"STOP command queued & executed: {cmd_id}", icon="🛑")
        else:
            st.warning(msg)
        st.rerun()

if is_test:
    with ctl_btn2:
        rst_disabled = not safety_summary["manual_stop_latched"]
        if st.button("🔄 Reset test device", disabled=rst_disabled, use_container_width=True, help="Clears platform stop latch on test device only after verifying 0 faults. Leaves pump OFF and valve CLOSED."):
            dev_norm = normalize_device_id(active_device)
            succ_rst, rst_cmd, msg_rst = request_simulator_reset(dev_norm, db_path=DB_PATH)
            if succ_rst:
                execute_simulator_cycle_for_device(dev_norm, db_path=DB_PATH)
                st.toast("Reset complete — stopped, ready for simulated start.", icon="✅")
            else:
                st.error(msg_rst)
            st.rerun()

    with ctl_btn3:
        start_disabled = (
            safety_summary["manual_stop_latched"] or
            not safety_summary.get("can_start_simulator", False) or
            safety_summary["reported_states"]["pump_state"] == "ON"
        )
        if st.button("▶️ Start simulated operation", disabled=start_disabled, use_container_width=True, help="Separate explicit action. Requires successful reset, fresh feedback, and zero faults."):
            dev_norm = normalize_device_id(active_device)
            succ_strt, strt_cmd, msg_strt = request_simulator_start(dev_norm, db_path=DB_PATH)
            if succ_strt:
                execute_simulator_cycle_for_device(dev_norm, db_path=DB_PATH)
                st.toast("Simulated operation started: Pump ON, Valve OPEN", icon="💧")
            else:
                st.error(msg_strt)
            st.rerun()

    # Simulator Fault Injection Panel for Testing
    with st.expander("🧪 Simulator Fault Injection & Verification Controls", expanded=False):
        st.caption("Inject simulated faults to verify that active faults block reset and start operations.")
        f_col1, f_col2, f_col3 = st.columns(3)
        cur_faults = dev_state.get("fault_codes", []) if dev_state else []

        with f_col1:
            if st.button("⚠️ Inject UV Fault (E02_UV_FAIL)", use_container_width=True):
                with get_db_connection(DB_PATH) as conn:
                    conn.execute("UPDATE device_state SET fault_codes = ?, local_interlock_state = 'TRIPPED' WHERE device_id = ?",
                                 (json.dumps(["E02_UV_FAIL"]), active_device))
                    conn.commit()
                st.toast("Simulated UV Fault injected", icon="⚠️")
                st.rerun()

        with f_col2:
            if st.button("⚠️ Inject Flow Loss (E01_FLOW_LOW)", use_container_width=True):
                with get_db_connection(DB_PATH) as conn:
                    conn.execute("UPDATE device_state SET fault_codes = ?, local_interlock_state = 'TRIPPED' WHERE device_id = ?",
                                 (json.dumps(["E01_FLOW_LOW"]), active_device))
                    conn.commit()
                st.toast("Simulated Flow Fault injected", icon="⚠️")
                st.rerun()

        with f_col3:
            if st.button("🧹 Clear Simulator Faults", use_container_width=True):
                with get_db_connection(DB_PATH) as conn:
                    conn.execute("UPDATE device_state SET fault_codes = '[]' WHERE device_id = ?", (active_device,))
                    conn.commit()
                st.toast("Simulator faults cleared", icon="✅")
                st.rerun()

        if cur_faults:
            st.warning(f"Active simulated faults on {active_device}: {cur_faults} (Blocks Reset & Start)")
        else:
            st.info(f"No active simulated faults on {active_device}.")

else:
    with ctl_btn2:
        st.info("🔒 Remote reset is disabled on real hardware. Resolve faults and reset locally on the ESP32.")
    with ctl_btn3:
        st.info("🔒 Remote start is disabled on real hardware. Physical operator action required.")

st.divider()


# ==========================================
# VIEW 1: OVERVIEW
# ==========================================
if selected_view == "Overview":
    st.subheader("Current Telemetry & Equipment Feedback")

    if dev_state is None:
        st.warning("Waiting for telemetry. No data packet has been received from this device yet.")

    # 6 Telemetry Metrics
    m_col1, m_col2, m_col3, m_col4, m_col5, m_col6 = st.columns(6)

    temp_val = dev_state.get("temp_C") if dev_state else None
    ph_val = dev_state.get("pH") if dev_state else None
    ec_val = dev_state.get("EC_uS_cm") if dev_state else None
    turb_val = dev_state.get("turbidity_NTU") if dev_state else None
    flow_val = dev_state.get("flow_L_min") if dev_state else None
    stag_val = dev_state.get("stagnation_hours") if dev_state else None

    with m_col1:
        st.metric("Temperature", format_reading(temp_val, "°C"))
    with m_col2:
        st.metric("pH Level", format_reading(ph_val, "pH", 2))
    with m_col3:
        st.metric("Conductivity", format_reading(ec_val, "µS/cm", 1))
    with m_col4:
        st.metric("Turbidity", format_reading(turb_val, "NTU", 2))
    with m_col5:
        st.metric("Flow Rate", format_reading(flow_val, "L/min", 2))
    with m_col6:
        st.metric("Stagnation", format_reading(stag_val, "hrs", 1))

    st.divider()

    # Reported Equipment States
    st.subheader("Reported Equipment Feedback (ESP32 Verified)")
    st.caption("Note: Reported feedback reflects physical device states received via MQTT telemetry, distinct from platform commands.")

    eq_col1, eq_col2, eq_col3, eq_col4 = st.columns(4)
    with eq_col1:
        p_state = dev_state.get("pump_state", "UNKNOWN") if dev_state else "UNKNOWN"
        st.write("**Misting Pump:**")
        if p_state == "ON":
            st.success("PUMP ON", icon="💧")
        elif p_state == "OFF":
            st.error("PUMP OFF (Safeguarded)", icon="🛑")
        else:
            st.info("PUMP UNKNOWN", icon="❓")

    with eq_col2:
        v_state = dev_state.get("valve_state", "UNKNOWN") if dev_state else "UNKNOWN"
        st.write("**Solenoid Valve:**")
        if v_state == "OPEN":
            st.success("VALVE OPEN", icon="🚰")
        elif v_state == "CLOSED":
            st.error("VALVE CLOSED", icon="🔒")
        else:
            st.info("VALVE UNKNOWN", icon="❓")

    with eq_col3:
        uv_state = dev_state.get("uv_status", "UNKNOWN") if dev_state else "UNKNOWN"
        st.write("**UV-C Disinfection:**")
        if uv_state == "OK":
            st.success("UV-C OPERATIONAL", icon="☀️")
        elif uv_state == "FAULT":
            st.error("UV-C FAULT DETECTED", icon="⚠️")
        else:
            st.info("UV-C UNKNOWN", icon="❓")

    with eq_col4:
        interlock_state = dev_state.get("local_interlock_state", "UNKNOWN") if dev_state else "UNKNOWN"
        st.write("**ESP32 Hardware Interlock:**")
        if interlock_state in ("NORMAL", "ARMED"):
            st.success(f"INTERLOCK: {interlock_state}", icon="🛡️")
        elif interlock_state == "TRIPPED":
            st.error("INTERLOCK TRIPPED", icon="🚨")
        else:
            st.info("INTERLOCK UNKNOWN", icon="❓")

    # Telemetry Metadata & Origin
    st.divider()
    meta_col1, meta_col2, meta_col3 = st.columns(3)
    with meta_col1:
        st.write("**Data Origin:**", dev_state.get("data_origin", "—") if dev_state else "—")
        if dev_state and dev_state.get("data_origin") == "SIMULATED":
            st.warning("⚠️ Warning: Data marked SIMULATED. Prohibited from triggering real hardware stop.")
    with meta_col2:
        st.write("**Device Timestamp:**", dev_state.get("last_device_timestamp", "—") if dev_state else "—")
    with meta_col3:
        st.write("**Server Received At:**", dev_state.get("last_received_at", "—") if dev_state else "—")

    # Recent Alerts & Faults
    st.divider()
    st.subheader("Recent Alerts & Faults")
    recent_events = get_events(device_id=active_device, limit=5, db_path=DB_PATH)
    if recent_events:
        for ev in recent_events:
            sev = ev.get("severity", "INFO")
            msg_str = f"**[{ev['timestamp']}]** ({ev['event_type']}) - {ev['message']}"
            if sev == "CRITICAL":
                st.error(msg_str)
            elif sev == "WARNING":
                st.warning(msg_str)
            else:
                st.info(msg_str)
    else:
        st.info("No recorded alerts or fault events.")


# ==========================================
# VIEW 2: TRENDS
# ==========================================
elif selected_view == "Trends":
    st.subheader("Historical Sensor Telemetry")

    t_col1, t_col2 = st.columns([1, 3])
    with t_col1:
        time_range = st.selectbox("Select Time Range", ["1 Hour", "6 Hours", "24 Hours", "All Available"])
        hours_map = {"1 Hour": 1.0, "6 Hours": 6.0, "24 Hours": 24.0, "All Available": None}
        hours_filter = hours_map[time_range]

    history = get_telemetry_history(device_id=active_device, hours=hours_filter, limit=1000, db_path=DB_PATH)

    if not history:
        st.info("No historical telemetry records found for this time range. Charts remain empty.")
    else:
        df = pd.DataFrame(history)
        df["received_at"] = pd.to_datetime(df["received_at"])
        df = df.sort_values("received_at")

        # CSV Export
        csv_data = df.to_csv(index=False)
        st.download_button(
            label="📥 Export Telemetry as CSV",
            data=csv_data,
            file_name=f"mistsafe_telemetry_{active_device}_{int(time.time())}.csv",
            mime="text/csv"
        )

        st.write("### Sensor Readings Over Time")
        chart_col1, chart_col2 = st.columns(2)

        with chart_col1:
            st.write("**Temperature (°C) & Flow Rate (L/min)**")
            temp_flow_df = df[["received_at", "temp_C", "flow_L_min"]].set_index("received_at")
            st.line_chart(temp_flow_df)

            st.write("**pH Level**")
            ph_df = df[["received_at", "pH"]].set_index("received_at")
            st.line_chart(ph_df)

        with chart_col2:
            st.write("**Electrical Conductivity (µS/cm)**")
            ec_df = df[["received_at", "EC_uS_cm"]].set_index("received_at")
            st.line_chart(ec_df)

            st.write("**Turbidity (NTU) & Stagnation (hrs)**")
            turb_df = df[["received_at", "turbidity_NTU", "stagnation_hours"]].set_index("received_at")
            st.line_chart(turb_df)


# ==========================================
# VIEW 3: SAMPLES & MICROSCOPY
# ==========================================
elif selected_view == "Samples & Microscopy":
    st.subheader("Microscopy Sample Intake & Laboratory Records")
    st.caption("Upload microscopy imagery (PNG/JPEG) with physical metadata. Safe file storage and validation enforced.")

    s_col1, s_col2 = st.columns([1, 1])

    with s_col1:
        st.write("### New Sample Registration")
        with st.form("sample_upload_form", clear_on_submit=True):
            sample_id_input = st.text_input("Sample ID", value=f"SMP-{int(time.time())}")
            collection_time_input = st.text_input("Collection Timestamp (UTC)", value=utc_now_iso())
            location_input = st.text_input("Sampling Location", value="Nozzle Manifold #1")
            imaging_method_input = st.selectbox(
                "Imaging Method",
                ["Brightfield 40x", "Phase Contrast 100x", "Epifluorescence", "Digital Holographic", "Other"]
            )
            data_origin_input = st.selectbox(
                "Data Origin",
                ["REAL", "REFERENCE"],
                help="Reference images from historical studies or CDC repositories must be labeled REFERENCE."
            )
            lab_method = st.text_input("Reference Lab Method (optional)", placeholder="e.g. ISO 11731 Culture / qPCR")
            lab_result = st.text_input("Reference Lab Result (optional)", placeholder="e.g. <100 CFU/L")

            uploaded_file = st.file_uploader("Upload Microscopy Image (PNG / JPEG max 10MB)", type=["png", "jpg", "jpeg"])

            submitted = st.form_submit_button("Register & Save Sample")
            if submitted:
                if not uploaded_file:
                    st.error("Please upload an image file.")
                else:
                    file_bytes = uploaded_file.read()
                    try:
                        safe_name, img_format = validate_image_file(uploaded_file.name, file_bytes)
                        save_path = os.path.join(UPLOAD_DIR, safe_name)
                        with open(save_path, "wb") as f:
                            f.write(file_bytes)

                        # Look up latest telemetry ID if real
                        assoc_telemetry = dev_state.get("id") if dev_state else None

                        save_sample_record(
                            sample_id=sample_id_input,
                            device_id=active_device,
                            collection_time=collection_time_input,
                            sampling_location=location_input,
                            imaging_method=imaging_method_input,
                            data_origin=data_origin_input,
                            image_path=save_path,
                            image_safe_name=safe_name,
                            reference_lab_method=lab_method if lab_method else None,
                            reference_lab_result=lab_result if lab_result else None,
                            associated_telemetry_id=assoc_telemetry,
                            db_path=DB_PATH
                        )
                        st.success(f"Sample '{sample_id_input}' saved successfully ({img_format}). Safe file: {safe_name}")
                        st.rerun()

                    except ValidationError as ve:
                        st.error(f"Image validation rejected: {str(ve)}")
                    except Exception as e:
                        st.error(f"Failed to save sample: {e}")

    with s_col2:
        st.write("### Recorded Samples")
        samples = get_samples(device_id=active_device, db_path=DB_PATH)
        if not samples:
            st.info("No sample image uploaded. Register a microscopy sample using the form.")
        else:
            sample_ids = [s["sample_id"] for s in samples]
            selected_smp_id = st.selectbox("Inspect Sample", sample_ids)
            selected_sample = next(s for s in samples if s["sample_id"] == selected_smp_id)

            if selected_sample["data_origin"] == "REFERENCE":
                st.warning("⚠️ REFERENCE SAMPLE: This image is historical/laboratory reference data, NOT current system water.")

            img_p = selected_sample["image_path"]
            if os.path.exists(img_p):
                try:
                    img = Image.open(img_p)
                    st.image(img, caption=f"Sample: {selected_sample['sample_id']} ({selected_sample['imaging_method']})", use_container_width=True)
                except Exception:
                    st.error("Unable to render image file.")
            else:
                st.warning("Image file path not found on disk.")

            st.write(f"**Sampling Location:** {selected_sample['sampling_location']}")
            st.write(f"**Collection Time:** {selected_sample['collection_time']}")
            st.write(f"**Origin:** {selected_sample['data_origin']}")
            if selected_sample.get("reference_lab_method"):
                st.write(f"**Lab Method:** {selected_sample['reference_lab_method']} | Result: {selected_sample.get('reference_lab_result', 'N/A')}")


# ==========================================
# VIEW 4: AI ANALYSIS
# ==========================================
elif selected_view == "AI Analysis":
    st.subheader("Local AI Inference & Risk Evaluation")
    st.caption("Inspect multimodal inference results. AI scores are transparently tracked with model versions and validation statuses.")

    # Status of AI service
    ai_status_col1, ai_status_col2 = st.columns([2, 1])
    with ai_status_col1:
        if not ai_health["available"]:
            st.error(f"AI Service is unavailable: {ai_health.get('reason', 'Connection refused')}")
            st.caption(f"Configured endpoint: `{AI_SERVICE_URL}`. Ensure `python3 ai_service.py` is running.")
        else:
            st.success(f"Connected to AI Service at `{AI_SERVICE_URL}` (Status: {ai_health.get('service_status', 'OK')})")

    with ai_status_col2:
        run_inference_btn = st.button("⚡ Run AI Inference Now", disabled=not ai_health["available"])

    st.divider()

    # Model Inferences
    latest_sensor_pred = get_latest_ai_prediction("SENSORS", db_path=DB_PATH)
    latest_vision_pred = get_latest_ai_prediction("IMAGE", db_path=DB_PATH)
    latest_comp_pred = get_latest_ai_prediction("COMPOSITE", db_path=DB_PATH)

    # Handle explicit run inference trigger
    if run_inference_btn:
        if dev_state:
            with st.spinner("Executing sensor model inference..."):
                latest_sensor_pred = ai_client.predict_sensors(dev_state, db_path=DB_PATH)
        else:
            st.warning("No telemetry available to run sensor inference.")

        # Check if samples available
        samples = get_samples(device_id=active_device, db_path=DB_PATH)
        if samples:
            latest_smp = samples[0]
            with st.spinner("Executing vision model inference on latest sample..."):
                latest_vision_pred = ai_client.predict_image(
                    sample_id=latest_smp["sample_id"],
                    image_path=latest_smp["image_path"],
                    sample_metadata=latest_smp,
                    db_path=DB_PATH
                )

        if st.session_state.ui_composite_enabled:
            latest_comp_pred = ai_client.compute_composite_score(
                vision_pred=latest_vision_pred,
                sensor_pred=latest_sensor_pred,
                vision_weight=st.session_state.ui_vision_weight,
                sensor_weight=st.session_state.ui_sensor_weight,
                db_path=DB_PATH
            )
            # Evaluate AI stop rule if enabled
            if st.session_state.ui_ai_stop_enabled:
                triggered, trig_msg = ai_client.check_and_apply_ai_stop_rule(
                    device_id=active_device,
                    composite_pred=latest_comp_pred,
                    ai_stop_enabled=st.session_state.ui_ai_stop_enabled,
                    threshold=st.session_state.ui_ai_stop_threshold,
                    db_path=DB_PATH
                )
                if triggered:
                    st.error(trig_msg)
                else:
                    st.info(trig_msg)

        st.rerun()

    # Scores Display
    score_col1, score_col2, score_col3 = st.columns(3)

    with score_col1:
        st.write("### Sensor Risk Score")
        if not latest_sensor_pred or latest_sensor_pred.get("risk_score") is None:
            st.metric("Sensor Score", "Unavailable")
            st.caption(f"Reason: {latest_sensor_pred.get('reason') if latest_sensor_pred else 'No inference executed yet'}")
        else:
            s_val = latest_sensor_pred["risk_score"]
            st.metric("Sensor Score", f"{s_val} / 100")
            st.progress(min(1.0, max(0.0, s_val / 100.0)))
            st.caption(f"Model: {latest_sensor_pred.get('model_name')} v{latest_sensor_pred.get('model_version')}")
            st.caption(f"Validation: {latest_sensor_pred.get('validation_status')} | Origin: {latest_sensor_pred.get('data_origin')}")

    with score_col2:
        st.write("### Vision Risk Score")
        if not latest_vision_pred or latest_vision_pred.get("risk_score") is None:
            st.metric("Vision Score", "Unavailable")
            st.caption(f"Reason: {latest_vision_pred.get('reason') if latest_vision_pred else 'No microscopy image evaluated'}")
        else:
            v_val = latest_vision_pred["risk_score"]
            st.metric("Vision Score", f"{v_val} / 100")
            st.progress(min(1.0, max(0.0, v_val / 100.0)))
            st.caption(f"Model: {latest_vision_pred.get('model_name')} v{latest_vision_pred.get('model_version')}")
            st.caption(f"Validation: {latest_vision_pred.get('validation_status')} | Origin: {latest_vision_pred.get('data_origin')}")

    with score_col3:
        st.write("### Composite Indicator")
        if not st.session_state.ui_composite_enabled:
            st.info("Composite scoring is disabled (default). Enable in research settings below.")
        else:
            if not latest_comp_pred or latest_comp_pred.get("risk_score") is None:
                st.metric("Composite Score", "Unavailable")
                st.caption(f"Reason: {latest_comp_pred.get('reason') if latest_comp_pred else 'Requires valid paired predictions'}")
            else:
                c_val = latest_comp_pred["risk_score"]
                st.metric("Composite Score", f"{c_val} / 100")
                st.progress(min(1.0, max(0.0, c_val / 100.0)))
                st.caption(f"Label: **Experimental composite indicator** (Never a validated biological probability)")
                st.caption(f"Origin: {latest_comp_pred.get('data_origin')}")

    st.divider()

    # Research Controls & Settings
    st.subheader("Research & Prototype Safety Rules")
    with st.expander("Configure Experimental AI Stop & Composite Weights", expanded=False):
        c_toggle = st.checkbox("Enable Experimental Composite Scoring", value=st.session_state.ui_composite_enabled)
        st.session_state.ui_composite_enabled = c_toggle

        col_w1, col_w2 = st.columns(2)
        with col_w1:
            w_vis = st.slider("Vision Weight", 0.0, 1.0, st.session_state.ui_vision_weight, 0.05)
            st.session_state.ui_vision_weight = w_vis
        with col_w2:
            w_sen = st.slider("Sensor Weight", 0.0, 1.0, round(1.0 - w_vis, 2), 0.05)
            st.session_state.ui_sensor_weight = w_sen

        st.divider()
        st.write("#### AI-Triggered Hardware Shutdown Rule")
        st.warning("⚠️ Notice: The threshold (e.g. 60.0) is a prototype configuration value, not a scientifically established safety limit.")
        ai_stop_toggle = st.checkbox("Enable Experimental AI Shutdown Rule", value=st.session_state.ui_ai_stop_enabled)
        st.session_state.ui_ai_stop_enabled = ai_stop_toggle

        thresh_val = st.number_input("Shutdown Threshold (Score > Threshold)", min_value=1.0, max_value=99.0, value=st.session_state.ui_ai_stop_threshold)
        st.session_state.ui_ai_stop_threshold = thresh_val


# ==========================================
# VIEW 5: EVENTS & CONTROLS
# ==========================================
elif selected_view == "Events & Controls":
    st.subheader("Hardware Commands, Acks & System Audit Log")

    ctl_col1, ctl_col2 = st.columns([1, 2])

    with ctl_col1:
        st.write("### Active Command Tracker")
        cmd_info = safety_summary.get("command_details")
        if not cmd_info:
            st.info("No command has been dispatched for this device.")
        else:
            st.write(f"**Command ID:** `{cmd_info['command_id']}`")
            st.write(f"**Action:** `{cmd_info['command_type']}`")
            st.write(f"**Origin:** `{cmd_info['origin']}`")
            st.write(f"**Created At:** {cmd_info['created_at']}")
            st.write(f"**Expires At:** {cmd_info.get('expires_at', '—')}")
            st.write(f"**Acknowledged At:** {cmd_info.get('acknowledged_at', 'Pending')}")
            if cmd_info.get("failure_reason"):
                st.error(f"Failure / Rejection Reason: {cmd_info['failure_reason']}")

        st.divider()
        st.write("### Latched Operator State")
        if safety_summary["manual_stop_latched"]:
            st.error("🛑 Manual stop is LATCHED.")
            st.caption("By safety architecture, falling AI scores or clearing alerts CANNOT unlatch this state. Physical inspection and hardware reset are required.")
        else:
            st.success("No manual stop latch active.")

    with ctl_col2:
        st.write("### Timestamped Audit & Event Log")
        filter_sev = st.selectbox("Filter Severity", ["ALL", "CRITICAL", "WARNING", "INFO"])
        all_events = get_events(device_id=active_device, severity=filter_sev, limit=100, db_path=DB_PATH)

        if not all_events:
            st.info("No events match the selected criteria.")
        else:
            event_rows = []
            for ev in all_events:
                event_rows.append({
                    "Timestamp (UTC)": ev["timestamp"],
                    "Severity": ev["severity"],
                    "Event Type": ev["event_type"],
                    "Message": ev["message"]
                })
            st.dataframe(pd.DataFrame(event_rows), use_container_width=True)

    st.divider()

    # Section: Real-Device Local Reset Contract & Verification
    st.subheader("Physical Hardware Local-Reset Event Specification")
    st.caption("Contract and verification tool for reconciling physical push-button resets on the ESP32 node.")

    rst_tab1, rst_tab2 = st.tabs(["Local-Reset Event Contract", "Simulate ESP32 Local-Reset Intake"])

    with rst_tab1:
        st.markdown("""
        **MQTT Topic:** `mistsafe/{device_id}/events`  
        **QoS:** 1 (Retained messages are rejected as historical)  
        **Event Type:** `LOCAL_RESET`

        When an operator resolves physical faults and pushes the physical reset button on the ESP32, the node publishes this event.
        The platform clears the manual stop latch **only** if:
        1. Event is fresh and not a retained MQTT message.
        2. Sequence number is strictly monotonic for the device `boot_id`.
        3. Reported `stop_generation` or `stop_command_id` matches the current stop latch (older stops cannot clear newer ones).
        4. Reported `outcome` is strictly `SUCCESS` and `remaining_faults` is empty.
        5. Fresh telemetry feedback confirms physical interlock is `NORMAL`/`ARMED` and misting pump is `OFF` and valve is `CLOSED`.
        """)

        st.code("""{
  "event_type": "LOCAL_RESET",
  "event_id": "rst-evt-esp32-00412",
  "device_id": "MISTSAFE-ESP32-01",
  "boot_id": "boot-88ef3",
  "seq": 1,
  "timestamp": "2026-10-12T14:30:00.000Z",
  "stop_command_id": "cmd-8f3b20c19a4e",
  "stop_generation": 1,
  "outcome": "SUCCESS",
  "remaining_faults": []
}""", language="json")

    with rst_tab2:
        st.write("#### Send Test Local-Reset Event to Ingestion Engine")
        st.caption("Test the contract rules and reconciliation logic directly against the active device state.")

        dev_st = get_device_state(active_device, db_path=DB_PATH) or {}
        cur_gen = dev_st.get("stop_generation", 1)
        cur_cmd_id = dev_st.get("current_stop_command_id", "")

        sim_c1, sim_c2 = st.columns(2)
        with sim_c1:
            t_evt_id = st.text_input("Event ID", value=f"rst-{int(time.time())}")
            t_boot_id = st.text_input("Boot / Session ID", value="boot-esp32-alpha")
            t_seq = st.number_input("Sequence Number", value=1, min_value=1)
            t_outcome = st.selectbox("Outcome", ["SUCCESS", "FAILED"])
        with sim_c2:
            t_gen = st.number_input("Stop Generation", value=cur_gen)
            t_cmd_id = st.text_input("Stop Command ID", value=cur_cmd_id)
            t_faults = st.text_input("Remaining Faults (comma-separated)", value="")

        if st.button("🚀 Process Local-Reset Event", type="primary", use_container_width=True):
            fault_list = [f.strip() for f in t_faults.split(",") if f.strip()]
            payload = {
                "event_type": "LOCAL_RESET",
                "event_id": t_evt_id,
                "device_id": active_device,
                "boot_id": t_boot_id,
                "seq": int(t_seq),
                "timestamp": utc_now_iso(),
                "stop_command_id": t_cmd_id,
                "stop_generation": int(t_gen),
                "outcome": t_outcome,
                "remaining_faults": fault_list
            }
            succ, res_msg = handle_local_reset_event(payload, is_retained=False, db_path=DB_PATH)
            if succ:
                st.success(f"✅ {res_msg}")
            else:
                st.error(f"❌ {res_msg}")
            st.rerun()

    # Reset Event Audit Log
    st.write("#### Recorded Reset Event Transitions (SQLite Persistence)")
    r_events = get_reset_events(device_id=active_device, limit=20, db_path=DB_PATH)
    if not r_events:
        st.info("No local reset events recorded for this device.")
    else:
        r_rows = []
        for re in r_events:
            r_rows.append({
                "Received (UTC)": re["received_at"],
                "Event ID": re["event_id"],
                "Seq": re["seq"],
                "Stop Gen": re["stop_generation"],
                "Outcome": re["outcome"],
                "Status": re["processed_status"]
            })
        st.dataframe(pd.DataFrame(r_rows), use_container_width=True)

# Auto-refresh loop handling
if st.session_state.ui_auto_refresh:
    time.sleep(st.session_state.ui_refresh_interval)
    st.rerun()
