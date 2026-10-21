# Presentation edition — 22 October 2026

The project scope is now a fully simulated, English/Arabic presentation site. Physical equipment integration and model training are outside this edition. The UI uses a forest-green sidebar, warm neutral surfaces, restrained lime accents, and bundled Manrope / IBM Plex Sans Arabic fonts.

## Current experience

- Sidebar navigation: Developer tools first, Overview, Sample explorer, Evidence library, How it works.
- No Trends, real/simulation selector, upload claims, model inference or hardware controls.
- Arabic switches document language and RTL layout, including sidebar position; language preference is stored locally when browser storage is available.
- Searchable/filterable/paginated sample explorer, actual CSV exports, accessible record dialog and selection into Overview.
- Demonstration scenarios select real rows from the supplied synthetic workbook rather than manufacturing readings.
- Research links remain explicitly separate from sample-linked evidence. Missing sample images are shown as missing, not substituted with unrelated imagery.

## Source material

The 80-record operational workbook drives the interface. Risk and safety rules reproduce its U/V/W formulas. The 400-record workbook is a separate downloadable archive, not combined with the operational collection. The research register supplies seven image-reference entries and two research dataset entries; its 100 training slots are empty templates, not collected samples.

Original workbook copies are in public/files. SHA-256 source hashes and original names are in src/presentation-data.json. Regenerate with `python scripts/prepare-presentation-data.py <source-folder>` only when the supplied workbooks change. The website never downloads or imports external research values at runtime.

## Running and deployment

Run `npm run dev`, or double-click Start MistSafe AI.cmd on the current Windows laptop. Only Node is needed for this edition. `npm run build` produces a static dist directory; relative asset paths and hash navigation support the GitHub Pages project URL. The Pages workflow builds on main and publishes dist only, excluding the former backend, database and uploads.

The original Express/Python implementation is retained for historical reference and can be launched via `npm run legacy:dev`. It is not used by the presentation build. Earlier development/status documents describe that previous scope.

## Validation

TypeScript and production build checks; browser checks in verify-presentation.cjs cover source downloads, sample filtering, CSV content, record dialog, source-based scenarios, language persistence, RTL, mobile overflow and absence of backend calls. Screenshot artifacts are ignored from Git. The browser helper uses this laptop's Edge/Playwright installation.

No measured biological accuracy, live sensor performance, or physical valve response is claimed. The Word document's numerical performance claims and the PowerPoint's former architecture have not been rewritten in this task.
