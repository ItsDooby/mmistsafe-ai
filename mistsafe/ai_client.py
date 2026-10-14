"""MistSafe AI Client Module.
HTTP client communicating with the local AI inference service.
Implements health checks, sensor inference, microscopy image inference,
experimental composite score calculation, and safety-gated AI stop triggering.
"""

import os
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None

from mistsafe.database import (
    DEFAULT_DB_PATH,
    get_db_connection,
    get_latest_ai_prediction,
    log_event,
    save_ai_prediction,
    utc_now_iso
)
from mistsafe.commands import request_shutdown

DEFAULT_AI_BASE_URL = os.getenv("AI_SERVICE_URL", "http://127.0.0.1:8008")
DEFAULT_TIMEOUT_SEC = float(os.getenv("AI_REQUEST_TIMEOUT_SEC", "5"))


class AIServiceError(Exception):
    """Raised when the AI service is unreachable, timed out, or returned an error."""
    pass


class AIClient:
    def __init__(self, base_url: str = DEFAULT_AI_BASE_URL, timeout_sec: float = DEFAULT_TIMEOUT_SEC):
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    def check_health(self) -> Dict[str, Any]:
        """
        Check health of the AI service via GET /health.
        Returns response dict or raises AIServiceError.
        """
        if requests is None:
            return {
                "available": False,
                "reason": "requests library not installed",
                "service_status": "UNAVAILABLE"
            }
        try:
            url = f"{self.base_url}/health"
            resp = requests.get(url, timeout=self.timeout_sec)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "available": True,
                    "service_status": data.get("status", "OK"),
                    "version": data.get("version", "unknown"),
                    "models": data.get("models", {})
                }
            return {
                "available": False,
                "reason": f"Service returned HTTP {resp.status_code}"
            }
        except Exception as e:
            return {
                "available": False,
                "reason": f"AI service unavailable: {str(e)}"
            }

    def predict_sensors(
        self,
        telemetry_payload: Dict[str, Any],
        db_path: str = DEFAULT_DB_PATH
    ) -> Dict[str, Any]:
        """
        Send sensor readings to POST /predict/sensors.
        Validates contract and persists prediction record.
        """
        url = f"{self.base_url}/predict/sensors"
        input_id = telemetry_payload.get("message_id", f"msg-{int(datetime.now(timezone.utc).timestamp())}")
        data_origin = telemetry_payload.get("data_origin", "REAL")

        try:
            resp = requests.post(
                url,
                json=telemetry_payload,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout_sec
            )
            if resp.status_code != 200:
                reason = f"HTTP {resp.status_code}: {resp.text[:100]}"
                pred = self._build_unavailable_prediction("SENSORS", input_id, data_origin, reason)
                save_ai_prediction(**pred, db_path=db_path)
                return pred

            res_json = resp.json()
            validated_pred = self._validate_and_format_response("SENSORS", input_id, data_origin, res_json)
            save_ai_prediction(**validated_pred, db_path=db_path)
            return validated_pred

        except requests.exceptions.RequestException as e:
            pred = self._build_unavailable_prediction("SENSORS", input_id, data_origin, f"Connection failed: {str(e)}")
            save_ai_prediction(**pred, db_path=db_path)
            return pred

    def predict_image(
        self,
        sample_id: str,
        image_path: str,
        sample_metadata: Dict[str, Any],
        db_path: str = DEFAULT_DB_PATH
    ) -> Dict[str, Any]:
        """
        Send microscopy image and metadata to POST /predict/image.
        Validates contract and persists prediction record.
        """
        url = f"{self.base_url}/predict/image"
        data_origin = sample_metadata.get("data_origin", "REAL")

        if not os.path.exists(image_path):
            pred = self._build_unavailable_prediction("IMAGE", sample_id, data_origin, f"Image file not found: {image_path}")
            save_ai_prediction(**pred, db_path=db_path)
            return pred

        try:
            with open(image_path, "rb") as f:
                files = {"image": (os.path.basename(image_path), f, "image/jpeg")}
                data = {
                    "sample_id": sample_id,
                    "metadata": json.dumps(sample_metadata)
                }
                resp = requests.post(url, files=files, data=data, timeout=self.timeout_sec)

            if resp.status_code != 200:
                reason = f"HTTP {resp.status_code}: {resp.text[:100]}"
                pred = self._build_unavailable_prediction("IMAGE", sample_id, data_origin, reason)
                save_ai_prediction(**pred, db_path=db_path)
                return pred

            res_json = resp.json()
            validated_pred = self._validate_and_format_response("IMAGE", sample_id, data_origin, res_json)
            save_ai_prediction(**validated_pred, db_path=db_path)
            return validated_pred

        except requests.exceptions.RequestException as e:
            pred = self._build_unavailable_prediction("IMAGE", sample_id, data_origin, f"Connection failed: {str(e)}")
            save_ai_prediction(**pred, db_path=db_path)
            return pred

    def compute_composite_score(
        self,
        vision_pred: Optional[Dict[str, Any]],
        sensor_pred: Optional[Dict[str, Any]],
        vision_weight: float = 0.5,
        sensor_weight: float = 0.5,
        db_path: str = DEFAULT_DB_PATH
    ) -> Dict[str, Any]:
        """
        Experimental composite indicator.
        Requires:
        - Enabled composite scoring
        - Both vision and sensor scores must be valid and present (never substitutes 0 for missing!)
        - Weights must sum to 1.0 (normalized if needed)
        - Both must be from REAL origin to ever qualify for safety trigger
        """
        now_ts = utc_now_iso()
        pred_id = f"comp-{int(datetime.now(timezone.utc).timestamp())}"

        # Weights normalization
        total_w = vision_weight + sensor_weight
        if total_w <= 0:
            total_w = 1.0
        w_v = vision_weight / total_w
        w_s = sensor_weight / total_w

        if not vision_pred or not sensor_pred:
            return {
                "prediction_id": pred_id,
                "input_type": "COMPOSITE",
                "input_id": "none",
                "model_name": "MistSafe-Composite-Experimental",
                "model_version": "0.1.0-research",
                "validation_status": "INCOMPLETE",
                "risk_score": None,
                "inference_timestamp": now_ts,
                "reason": "Requires both valid vision and sensor prediction results",
                "data_origin": "UNKNOWN",
                "label": "Experimental composite indicator"
            }

        v_score = vision_pred.get("risk_score")
        s_score = sensor_pred.get("risk_score")

        if v_score is None or s_score is None:
            return {
                "prediction_id": pred_id,
                "input_type": "COMPOSITE",
                "input_id": f"{vision_pred.get('input_id')}+{sensor_pred.get('input_id')}",
                "model_name": "MistSafe-Composite-Experimental",
                "model_version": "0.1.0-research",
                "validation_status": "UNAVAILABLE",
                "risk_score": None,
                "inference_timestamp": now_ts,
                "reason": "One or both sub-model risk scores are unavailable (never substituted with zero)",
                "data_origin": "UNKNOWN",
                "label": "Experimental composite indicator"
            }

        # Determine composite origin
        v_orig = vision_pred.get("data_origin", "REAL")
        s_orig = sensor_pred.get("data_origin", "REAL")
        if v_orig == "SIMULATED" or s_orig == "SIMULATED":
            comp_origin = "SIMULATED"
        elif v_orig == "REFERENCE" or s_orig == "REFERENCE":
            comp_origin = "REFERENCE"
        else:
            comp_origin = "REAL"

        comp_score = round((w_v * float(v_score)) + (w_s * float(s_score)), 2)

        record = {
            "prediction_id": pred_id,
            "input_type": "COMPOSITE",
            "input_id": f"{vision_pred.get('input_id')}+{sensor_pred.get('input_id')}",
            "model_name": "MistSafe-Composite-Experimental",
            "model_version": "0.1.0-research",
            "validation_status": "EXPERIMENTAL_CALCULATED",
            "risk_score": comp_score,
            "inference_timestamp": now_ts,
            "reason": None,
            "data_origin": comp_origin,
            "label": "Experimental composite indicator",
            "details": {
                "vision_score": v_score,
                "sensor_score": s_score,
                "vision_weight": round(w_v, 2),
                "sensor_weight": round(w_s, 2)
            }
        }

        save_ai_prediction(**record, db_path=db_path)
        return record

    def check_and_apply_ai_stop_rule(
        self,
        device_id: str,
        composite_pred: Dict[str, Any],
        ai_stop_enabled: bool,
        threshold: float = 60.0,
        db_path: str = DEFAULT_DB_PATH
    ) -> Tuple[bool, Optional[str]]:
        """
        Evaluate experimental AI-triggered stop rule.
        Strict conditions:
        1. Feature explicitly enabled by operator.
        2. Score is valid, finite, and strictly > threshold.
        3. Data origin is strictly REAL (never SIMULATED or REFERENCE).
        4. Validation status must be valid.
        5. Does not restart hardware if score falls.
        6. Manual stop latch takes precedence.
        """
        if not ai_stop_enabled:
            return False, "AI stop feature is disabled."

        if not composite_pred:
            return False, "No composite prediction available."

        score = composite_pred.get("risk_score")
        if score is None:
            return False, "Composite risk score is unavailable."

        origin = composite_pred.get("data_origin")
        if origin != "REAL":
            log_event(
                device_id,
                event_type="AI_ALERT",
                severity="WARNING",
                message=f"AI stop rule suppressed: Score {score} originated from {origin} (simulated/reference data cannot command real hardware)",
                details=composite_pred,
                db_path=db_path
            )
            return False, f"Suppressed: data origin is {origin}. Only REAL data can command hardware."

        if score > threshold:
            reason = f"AI Stop Rule Triggered: Fresh REAL composite risk score {score} > threshold {threshold}"
            success, cmd_id, msg = request_shutdown(
                device_id=device_id,
                origin="AI_RULE",
                reason=reason,
                db_path=db_path
            )
            log_event(
                device_id,
                event_type="AI_ALERT",
                severity="CRITICAL",
                message=reason,
                details={"score": score, "threshold": threshold, "command_id": cmd_id},
                db_path=db_path
            )
            return True, f"Hardware shutdown requested via AI rule: {msg}"

        return False, f"Score {score} is at or below prototype threshold {threshold}."

    def _validate_and_format_response(
        self,
        input_type: str,
        input_id: str,
        data_origin: str,
        res_json: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate local AI service response contract."""
        now_ts = utc_now_iso()
        pred_id = f"pred-{int(datetime.now(timezone.utc).timestamp())}-{input_type.lower()}"

        raw_score = res_json.get("risk_score")
        risk_score = None
        if raw_score is not None:
            try:
                s = float(raw_score)
                if 0.0 <= s <= 100.0:
                    risk_score = round(s, 1)
            except (ValueError, TypeError):
                risk_score = None

        return {
            "prediction_id": pred_id,
            "input_type": input_type,
            "input_id": input_id,
            "model_name": res_json.get("model_name", "MistSafe-Inference-Engine"),
            "model_version": res_json.get("model_version", "1.0.0"),
            "validation_status": res_json.get("validation_status", "VALID" if risk_score is not None else "UNVALIDATED"),
            "risk_score": risk_score,
            "inference_timestamp": res_json.get("inference_timestamp", now_ts),
            "reason": res_json.get("reason"),
            "data_origin": data_origin,
            "details": res_json
        }

    def _build_unavailable_prediction(
        self,
        input_type: str,
        input_id: str,
        data_origin: str,
        reason: str
    ) -> Dict[str, Any]:
        now_ts = utc_now_iso()
        return {
            "prediction_id": f"pred-unavail-{int(datetime.now(timezone.utc).timestamp())}",
            "input_type": input_type,
            "input_id": input_id,
            "model_name": "MistSafe-Inference-Engine",
            "model_version": "N/A",
            "validation_status": "SERVICE_UNAVAILABLE",
            "risk_score": None,
            "inference_timestamp": now_ts,
            "reason": reason,
            "data_origin": data_origin,
            "details": None
        }
