# MistSafe product revision — 22 October 2026

## Removed from the active interface

- Developer tools, Evidence library, How it works, and all retired routes.
- Green UI theme (the incoming mist particles deliberately retain the green requested for the logo).
- Former promotional sidebar, edition stamp, source-download cards, and obsolete captions.
- Former IDs, unpaired image-reference substitution, hardware controls and backend requests.

## Added

- Seven analysis pages, blue/navy hierarchy, risk-first Overview, linked KPI cards, distributions and priority records.
- Full English/Arabic layouts, collapsible sidebar, keyboard focus, reduced-motion logo, responsive tables/drawers, empty/loading/error states.
- Shared source module with coordinated record replay and pause/resume.
- Search, category/risk filtering, ascending/descending risk sorting, pagination, CSV export and sample detail drawers.
- Reproducible sensor model, signed contribution charts, coefficient importance, feature/score correlations and uncertainty explanation.
- Microscopy inspection shell with unavailable quality/probability states; zoom remains disabled because there are no supplied image files.
- Threshold-derived alerts, local acknowledgement/resolution, mandatory verification notes for stop flags, persisted event log.
- Live configuration count preview, validated bands, alert enable/severity, fusion preference, units, language and replay interval.
- Build-computed held-out metrics and Wilson confidence intervals with explicit research-label limitations.

## Data truth

The current source files contain research-generated labels, not laboratory ground truth. The sensor model evaluates label agreement only. The first run yielded TP 19 / TN 23 / FP 9 / FN 9 on 60 held-out records. The UI reads these computed values from static JSON and never embeds fixed evaluation values.

No source image files were supplied, so biological image recognition, class probabilities, compressed microscopy images and real image hold-out performance cannot be implemented from these inputs. The app reports this directly. The available 100-slot pairing template is not training data. No unrelated reference image is assigned to a sample.

## Checks

- TypeScript and static build pass.
- Browser suite covers seven routes, collapsed sidebar, search/filter/sort/pagination, CSV, sample drawer, alert verification/persistence, settings validation/persistence, units, replay and English/Arabic mobile layouts.
- Independent data checks recompute metrics and inference, verify split separation and safety flags, and ensure image scores remain missing.
- Active source and HTML scanned for the requested retired terminology and network paths; no matches. Archived interfaces are outside src and are not shipped.
- Source/asset files remain well below 50 MB; site is static. The original backend remains in repository history/source only and is not a runtime dependency.

The existing QR URL is unchanged. Original Word and PowerPoint deliverables remain untouched.
