"""MistSafe AI Validation Module.
Strict validation for telemetry schemas, finite numbers, nullable sensor fields,
equipment states, timestamps, and microscopy image files.
"""

import math
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

VALID_DATA_ORIGINS = {"REAL", "SIMULATED", "REFERENCE"}
VALID_EQUIPMENT_STATES = {"ON", "OFF", "OPEN", "CLOSED", "OK", "FAULT", "UNKNOWN", "ARMED", "TRIPPED", "NORMAL"}
NUMERIC_SENSOR_FIELDS = {
    "temp_C",
    "pH",
    "EC_uS_cm",
    "turbidity_NTU",
    "flow_L_min",
    "stagnation_hours",
    "water_distance_cm"
}

# Plausible physical sanity bounds (non-finite or NaN strictly rejected; out-of-range flagged)
SENSOR_BOUNDS = {
    "temp_C": (-20.0, 100.0),
    "pH": (0.0, 14.0),
    "EC_uS_cm": (0.0, 100000.0),
    "turbidity_NTU": (0.0, 5000.0),
    "flow_L_min": (0.0, 500.0),
    "stagnation_hours": (0.0, 87600.0),
    "water_distance_cm": (0.0, 5000.0),
}


def calculate_water_fill_percent(
    water_distance_cm: Optional[float],
    empty_distance_cm: Optional[float],
    full_distance_cm: Optional[float]
) -> Tuple[Optional[float], Optional[str]]:
    """
    Calculate fill percentage according to contract:
    fill_percent = 100 * (empty_distance_cm - water_distance_cm) / (empty_distance_cm - full_distance_cm)
    Requires: empty_distance_cm > full_distance_cm.
    Returns (fill_percent, status_or_warning).
    """
    if water_distance_cm is None:
        return None, "LEVEL_UNAVAILABLE"
    if not math.isfinite(water_distance_cm) or water_distance_cm < 0:
        return None, "INVALID_READING"
    if empty_distance_cm is None or full_distance_cm is None:
        return None, "CALIBRATION_REQUIRED"
    if not (math.isfinite(empty_distance_cm) and math.isfinite(full_distance_cm)):
        return None, "CALIBRATION_REQUIRED"
    if empty_distance_cm <= full_distance_cm:
        return None, "CALIBRATION_REQUIRED"

    denom = empty_distance_cm - full_distance_cm
    fill_pct = 100.0 * (empty_distance_cm - water_distance_cm) / denom

    status = "OK"
    if fill_pct < 0.0 or fill_pct > 100.0:
        status = "OUT_OF_RANGE"

    return fill_pct, status


class ValidationError(Exception):
    """Raised when incoming telemetry or payload fails contract validation."""
    pass


def parse_and_validate_timestamp(ts_value: Any) -> str:
    """
    Validate that timestamp is a valid UTC timestamp (ISO 8601 string or numeric unix epoch).
    Returns normalized ISO-8601 UTC string.
    """
    if ts_value is None:
        raise ValidationError("Missing required 'timestamp' field")

    if isinstance(ts_value, (int, float)):
        if not math.isfinite(ts_value) or ts_value < 0:
            raise ValidationError("Numeric timestamp must be a finite positive number")
        try:
            return datetime.fromtimestamp(ts_value, timezone.utc).isoformat()
        except Exception as e:
            raise ValidationError(f"Invalid epoch timestamp {ts_value}: {e}")

    if isinstance(ts_value, str):
        cleaned = ts_value.strip()
        if not cleaned:
            raise ValidationError("Empty timestamp string")
        # Try standard ISO format
        try:
            dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
            # Ensure it is timezone-aware
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            pass

        # Try common fallback patterns
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except ValueError:
                continue

        raise ValidationError(f"Cannot parse timestamp '{ts_value}'. Must be ISO-8601 UTC string or epoch number.")

    raise ValidationError(f"Invalid timestamp type: {type(ts_value)}")


def validate_finite_number(field_name: str, value: Any) -> Optional[float]:
    """
    Ensure value is either None (missing) or a finite float.
    Never converts None or invalid to 0.0!
    """
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValidationError(f"Field '{field_name}' cannot be a boolean")

    try:
        f_val = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"Field '{field_name}' must be a numeric value or null, got '{value}'")

    if not math.isfinite(f_val):
        raise ValidationError(f"Field '{field_name}' must be finite (got NaN or Infinity)")

    # Sanity bounds check
    if field_name in SENSOR_BOUNDS:
        min_b, max_b = SENSOR_BOUNDS[field_name]
        if f_val < min_b or f_val > max_b:
            raise ValidationError(f"Field '{field_name}' value {f_val} is outside physical limits [{min_b}, {max_b}]")

    return f_val


def validate_telemetry_payload(data: Any) -> Dict[str, Any]:
    """
    Validate incoming MQTT telemetry JSON dictionary against the MistSafe contract.
    Returns cleaned, strongly-typed dictionary.
    Raises ValidationError on violation.
    """
    if not isinstance(data, dict):
        raise ValidationError(f"Telemetry payload must be a JSON object, got {type(data).__name__}")

    # 1. Required header fields
    device_id = data.get("device_id")
    if not device_id or not isinstance(device_id, str) or not device_id.strip():
        raise ValidationError("Missing or invalid 'device_id'")
    device_id = device_id.strip()

    message_id = data.get("message_id")
    if not message_id or not isinstance(message_id, str) or not message_id.strip():
        raise ValidationError("Missing or invalid 'message_id'")
    message_id = message_id.strip()

    raw_ts = data.get("timestamp")
    validated_ts = parse_and_validate_timestamp(raw_ts)

    schema_version = data.get("schema_version", "1.0")
    if not isinstance(schema_version, str):
        schema_version = str(schema_version)

    data_origin = data.get("data_origin", "REAL")
    if isinstance(data_origin, str):
        data_origin = data_origin.strip().upper()
    if data_origin not in VALID_DATA_ORIGINS:
        raise ValidationError(f"Invalid 'data_origin': '{data_origin}'. Allowed: {sorted(list(VALID_DATA_ORIGINS))}")

    cleaned: Dict[str, Any] = {
        "device_id": device_id,
        "message_id": message_id,
        "timestamp": validated_ts,
        "schema_version": schema_version,
        "data_origin": data_origin
    }

    # 2. Nullable numeric sensor fields
    for field in NUMERIC_SENSOR_FIELDS:
        if field in data:
            cleaned[field] = validate_finite_number(field, data[field])
        else:
            cleaned[field] = None

    # 3. Equipment fields (feedback)
    sensor_status = data.get("sensor_status")
    if sensor_status is not None and not isinstance(sensor_status, dict):
        raise ValidationError("'sensor_status' must be an object/dict if provided")
    cleaned["sensor_status"] = sensor_status if isinstance(sensor_status, dict) else {}

    # State enums
    for state_field, default_val in [
        ("uv_status", "UNKNOWN"),
        ("pump_state", "UNKNOWN"),
        ("valve_state", "UNKNOWN"),
        ("local_interlock_state", "UNKNOWN")
    ]:
        val = data.get(state_field)
        if val is None:
            cleaned[state_field] = default_val
        elif isinstance(val, str):
            val_upper = val.strip().upper()
            if val_upper in VALID_EQUIPMENT_STATES:
                cleaned[state_field] = val_upper
            else:
                cleaned[state_field] = default_val
        else:
            cleaned[state_field] = default_val

    # Fault codes list
    fault_codes = data.get("fault_codes")
    if fault_codes is None:
        cleaned["fault_codes"] = []
    elif isinstance(fault_codes, list):
        cleaned["fault_codes"] = [str(c).strip() for c in fault_codes if str(c).strip()]
    elif isinstance(fault_codes, str) and fault_codes.strip():
        cleaned["fault_codes"] = [fault_codes.strip()]
    else:
        raise ValidationError("'fault_codes' must be an array of strings if provided")

    return cleaned


def validate_image_file(filename: str, file_bytes: bytes, max_bytes: int = 10 * 1024 * 1024) -> Tuple[str, str]:
    """
    Validate image file size, extension, and content magic bytes (PNG or JPEG).
    Returns (safe_filename, detected_format).
    Raises ValidationError on security violation or corrupted image.
    """
    if not file_bytes:
        raise ValidationError("Uploaded file is empty (0 bytes)")

    if len(file_bytes) > max_bytes:
        raise ValidationError(f"File size exceeds maximum limit of {max_bytes // (1024 * 1024)}MB")

    # Magic bytes check
    is_png = file_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    is_jpeg = file_bytes.startswith(b"\xff\xd8\xff")

    if not (is_png or is_jpeg):
        raise ValidationError("File content is not a valid PNG or JPEG image")

    detected_format = "PNG" if is_png else "JPEG"
    ext = ".png" if is_png else ".jpg"

    # Sanitize base filename and prevent path traversal
    base_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", os.path.splitext(os.path.basename(filename))[0])
    if not base_name:
        base_name = "sample"

    unique_token = uuid.uuid4().hex[:8]
    safe_filename = f"{base_name}_{unique_token}{ext}"

    return safe_filename, detected_format
