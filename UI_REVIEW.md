# MistSafe AI — UI review

The redesign uses the requested light direction: warm off-white background, white surfaces, charcoal text and muted blue actions/water. It uses the existing React/TypeScript frontend. No backend telemetry, command state machine or hardware-control rules were rewritten.

## Structure

- Header: device selection, explicit simulated/real source, separate connection/freshness, accessible shutdown.
- Overview: operating summary; calibrated cylinder with aligned numeric readings; device-reported equipment strip; expandable calibration; attention items with next steps; compact stored flow history and recent activity.
- Trends: existing charts and loaded-record CSV export. The 500-record limit is disclosed rather than presented as a complete range export.
- Samples: image → metadata → save; filename/read progress/preview; contextual errors; reference labels; repository retrieval before success.
- AI analysis: unavailable service and unavailable scores remain explicit; pending action and errors are distinct from successful results. Model details are secondary.
- Activity: device/command details and full event log.
- Developer tools: fault/missing/out-of-limit injection, pause/resume, simulated level controls, explicit test packet generation, reset-event testing, raw state and actual test execution output.

Calibration and sample inputs remain stable during polling. Inputs are labelled, keyboard focus is visible, mobile sections stack, and reduced-motion settings disable transitions. The cylinder remains read-only and encodes usable fill height, not volume or water safety.

## Evidence

Checks were performed in focused browser runs while using `cylinder-review.db`; the original `mistsafe.db` was not used for runtime checks.

- Primary-device empty readings/unknown feedback and simulated-device source labelling.
- Periodic readings and calibrated cylinder retained.
- Shutdown → reset leaves stopped → explicit start.
- UV fault visible on Overview and simulated start blocked.
- Trends CSV downloads stored simulator readings.
- Reference test PNG/metadata persist through browser reload. Duplicate save displays an error and retains the selected file. Metadata/file selection survive polling.
- AI request with no service reports unavailable.
- Command-line suite: 38 passed. Web-triggered execution also returned 38 passed after fixing a Windows temporary-directory permission issue. The real response is in `redesign-test-run.json`.
- Desktop and 390px mobile screenshots were inspected. Overview, Samples, AI, Developer tools and Activity have no document horizontal overflow in the checked states. Pause/stale/resume retained the last cylinder fill and restored fresh readings after resume. Accumulated results from the focused runs are recorded in `redesign-browser-results.json`.

Preview: `http://127.0.0.1:3000` while the local server is running. Representative images: `redesign-desktop.png`, `redesign-mobile.png`, `redesign-primary.png`, `redesign-fault.png`, and `redesign-samples.png`.

Browser helpers use local Edge/Playwright paths and are environment-specific. This is software/simulator verification; it does not verify a broker, ESP32, ultrasonic hardware, physical shutdown, AI model accuracy, treatment performance or water safety.
