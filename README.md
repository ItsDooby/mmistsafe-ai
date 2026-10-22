# MistSafe AI

Bilingual water-quality analysis and sample review, hosted as a static site on GitHub Pages.

Public site: https://itsdooby.github.io/mmistsafe-ai/

## Interface

Overview, Samples, Sensor AI, Microscopy AI, Alerts, Performance and Configuration. English and Arabic include RTL layouts. The navy sidebar collapses and carries an animated SVG logo with a reduced-motion alternative. Settings and review events persist in the visitor's browser.

The active frontend is `src/product`, entered through `src/main.tsx`. Earlier interfaces are retained under `legacy/ui` and are not built or routed. No backend calls or physical integration are used by the site.

## Data and model boundaries

Only the supplied files are inputs. The 80-record operational collection drives sample review. A deterministic L2 logistic regression trains on the 280 training rows in the earlier 400-record collection. Sixty existing test rows are held out from fitting and normalization; 60 validation rows are unused. The labels are research-generated, not independently verified laboratory outcomes. Metrics therefore measure reference-label agreement, not biological detection accuracy or field performance.

There are no matched image files or image-quality labels. Microscopy correctly reports inconclusive, probabilities are unavailable, and no image classifier is trained. Missing image evidence is never treated as a zero score. Combined results currently use sensor evidence only. The existing research image links are not substituted as paired sample images.

Safety-stop flags are evaluated independently from the model. Alert closure requires a logged verification note for stop conditions, and does not clear the source condition or authorize restarting. Review records are local browser records, not a central audit service.

## Rebuild analysis

Python 3.10+ standard library only:

```powershell
python scripts/build_data.py
python scripts/check_analysis.py
```

The script reads the supplied Excel files in `public/files`, writes static analysis JSON in `public/analysis`, and computes evaluation metrics from held-out predictions. `provenance.json` records input hashes. No image outputs are created when no matched image files exist.

## Run and build

Use Node 22.12+ (CI uses Node 24):

```powershell
npm install
npm run dev
```

Open the printed URL ending in `/mmistsafe-ai/`. On the development laptop, double-click **Start MistSafe AI.cmd**. It uses the available Node executable and launches the site automatically.

```powershell
npm run lint
npm run build
npm run preview
```

Hash navigation and the `/mmistsafe-ai/` build base support GitHub Pages. The workflow rebuilds and verifies model outputs, checks TypeScript, builds the static bundle, and deploys on pushes to main. Model details, performance and microscopy metadata load only when their screens are opened. The central `src/product/data.ts` module owns replay and selected sample state.

Fonts and their licences are bundled locally. No external resources are needed to render the interface.

## Verification

`scripts/check_analysis.py` independently recomputes metrics, checks training/evaluation separation, verifies scores and independent stop flags, and asserts missing-image handling. `verify-product.cjs` exercises all seven routes, desktop/mobile and Arabic/English, sidebar collapse, filters/sorting, CSV, drawers, local alert logs, settings and replay. The browser helper uses this development laptop's Edge/Playwright paths. Set `PREVIEW_URL` to test a deployed build.

`PRODUCT_STATUS.md` documents the changes and remaining limitations. The older status files describe previous project scopes. The presentation QR in `presentation-assets` still points to the same public site. The submitted PowerPoint and Word files have not been edited in this revision.
