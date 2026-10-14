# MistSafe AI

Integration prototype for an ESP32 water-misting project. Physical safety logic belongs on the device. A platform command or reported output state does not prove physical shutdown or water safety. No real model or hardware operation has been validated here.

## Current architecture

The main interface is React/TypeScript (`src/App.tsx`), served by Express (`server.ts`) with Vite in development. Express invokes `mistsafe/bridge.py` using argument arrays, without shell interpolation. The Python modules validate telemetry and persist shared state/history in SQLite WAL. `app.py` is a separate older Streamlit interface; it is not the React application's entry point.

The web server emits simulator telemetry every five seconds into the existing Python validation/storage path. This direct-database simulator does not demonstrate MQTT delivery. `mqtt_worker.py` is a separate optional worker for broker/device integration. Run one web-server instance to avoid duplicate simulator timers.

## Local startup

Use Node 22.12+ and Python 3.10+. From the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm install
npm run dev
```

The server automatically uses `.venv/Scripts/python.exe` on Windows or `.venv/bin/python` elsewhere. Set `PYTHON_EXECUTABLE` to an alternative interpreter if needed. Dependencies are checked at startup; missing packages fail visibly rather than triggering automatic global installation.

Open http://127.0.0.1:3000. Copy `.env.example` to `.env` for optional configuration. `DATABASE_PATH` defaults to `mistsafe.db`; use a separate file for testing. No AI service is needed for monitoring. The primary device remains unavailable until telemetry arrives.

```powershell
npm run lint
npm run build
.\.venv\Scripts\python.exe -m pytest tests -q
```

For MQTT integration, separately run `.venv/Scripts/python.exe mqtt_worker.py` after configuring your broker. MQTT/hardware operation requires separate verification. Browser test reset submissions are restricted to simulator devices; real reset events must use the worker's device event path.

## Water-level cylinder

The reusable SVG cylinder is read-only. It displays `water_distance_cm` using per-device calibration:

`fill_percent = 100 * (empty_distance_cm - water_distance_cm) / (empty_distance_cm - full_distance_cm)`

This is usable fill height, not litres or arbitrary tank-volume percentage. Overview's expandable device settings save calibration. Real devices receive no calibration defaults. Empty distance must exceed full distance; distances must be finite and nonnegative.

Simulated level controls are under Developer tools; Overview keeps calibration in expandable Device settings. The simulator seeds 40 cm empty / 10 cm full only on first creation. Existing saved calibration is preserved; use **Use test calibration** in the simulator level tools to select the requested test endpoints. The 0/25/50/75/100% selections produce 40/32.5/25/17.5/10 cm. Selection persists until changed and enters on the next periodic update. Missing/fault modes publish unavailable readings. Pausing telemetry preserves the last reading and becomes stale after 15 seconds. The drawing clamps out-of-range fills while preserving the raw distance and warning. Reduced-motion preferences disable the visual transition.

## Verification limits and remaining work

See `UI_REVIEW.md` for the light interface layout and browser checks, and `DEVELOPMENT_STATUS.md` for remaining work. Automated tests verify software behavior only; they do not verify physical equipment, treatment effectiveness, model accuracy, or complete MQTT integration. The UI verification checklist describes intended checks, not individual test-pass evidence.
