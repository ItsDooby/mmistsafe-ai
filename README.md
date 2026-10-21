# MistSafe AI

A bilingual English/Arabic demonstration platform for exploring supplied water-quality sample records and research references. The current edition is fully simulated and runs entirely in the browser.

## Current scope

- Forest-green sidebar, warm neutral surfaces, responsive layouts and full Arabic RTL navigation.
- Overview of a selected source sample with seven measurements and the workbook's illustrative risk formula.
- Searchable, filterable, paginated sample explorer with record details and CSV export.
- Developer tools select recorded scenarios; no new readings are manufactured.
- Evidence library includes the three supplied workbooks and their research/image reference links.
- No live devices, model training, image recognition, backend service or Python runtime is required.

The 80-record operational workbook drives the interface. The earlier 400-record workbook is a separate downloadable source. The research register contains seven image references, two dataset references and an empty paired-data template. Reference images are not substituted for missing sample images. Simulated laboratory labels are not presented as verified results.

## Run locally

Install Node 22.12+ (Node 24 is used for deployment), then:

```powershell
npm install
npm run dev
```

Open the URL printed in the terminal. On the existing Windows laptop, double-click **Start MistSafe AI.cmd**. This launcher uses an installed Node or the existing bundled Node and opens the browser automatically. Keep its terminal open while using the app. If 3000 is occupied, Vite selects the next free port.

## Build and publish

```powershell
npm run lint
npm run build
npm run preview
```

The static `dist` folder is the complete presentation website. `.github/workflows/pages.yml` installs from the pnpm lockfile, checks TypeScript, builds and deploys to GitHub Pages on pushes to main. Enable GitHub Actions as the Pages publishing source. Relative asset URLs and hash navigation support the repository path and direct tab links.

Public URL: https://itsdooby.github.io/mmistsafe-ai/

## Data and evidence

`src/presentation-data.json` contains extracted values, source filenames and SHA-256 hashes. Original workbooks are copied unchanged into `public/files`. `scripts/prepare-presentation-data.py` regenerates the data from the supplied files using Python's standard library; Python is needed only to regenerate source assets, not to run the platform.

Fonts are bundled with their open licences in `public/fonts`, so the interface has no runtime dependency on a font CDN. Research links open external sources only when a visitor selects them.

See `PRESENTATION_STATUS.md` for the current implementation and verification scope. `verify-presentation.cjs` exercises the main browser workflows on the development preview; its Edge/Playwright paths are specific to the development laptop.

## Earlier prototype

The earlier Express/Python/SQLite implementation remains in the repository for historical reference and is not included in the browser entry point. Its optional command is `npm run legacy:dev`. `DEVELOPMENT_STATUS.md` and `UI_REVIEW.md` describe that earlier hardware-oriented prototype, not this presentation edition.

No physical valve response, biological accuracy, or water-safety performance has been validated by this presentation.
