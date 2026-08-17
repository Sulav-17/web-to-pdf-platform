# CleanPDF Chrome extension (Manifest V3)

Turn your open tabs into one clean, bookmarked PDF.

## Commands

```bash
npm install          # install dev dependencies
npm run typecheck    # tsc --noEmit
npm run lint         # biome check
npm run test         # vitest
npm run build        # two-pass vite build + production audit
npm run dist         # build + ZIP into release/
npm run icons        # regenerate public/icons/*.png
```

`npm run verify` runs typecheck + lint + test + build in one go.

## Load the unpacked build

1. `npm run build` (output lands in `apps/extension/dist`).
2. Open `chrome://extensions`.
3. Turn on **Developer mode** (top right).
4. Click **Load unpacked** and select `apps/extension/dist`.
5. Pin CleanPDF to the toolbar.

The distributable ZIP is written to
`apps/extension/release/cleanpdf-extension-v<version>.zip`.

## Build layout

Two Vite passes, because MV3 needs different module formats:

- `vite.config.ts` — popup, options and print pages plus the ES-module service
  worker.
- `vite.inject.config.ts` — `src/inject/extract-entry.ts` bundled as a
  self-contained **IIFE** (`dist/inject/extract.js`), because
  `chrome.scripting.executeScript({ files })` cannot load ES modules. Mozilla
  Readability is bundled here, so the shipped package loads no remote code.

`npm run audit` fails the build on secrets, test keys, localhost assumptions,
remote code loaders, or any permission beyond the four approved ones.

## Permissions

Exactly four: `activeTab`, `storage`, `scripting`, `tabs`. No host permissions.

### Important limitation

Without host permissions, Chrome grants script access to a tab only via
`activeTab` — i.e. a tab where **the user has invoked the extension**. So:

- **Reading packs:** to include a tab, switch to it and click the CleanPDF icon
  once. The popup flags any tab it cannot read yet and asks before building a
  partial pack; it never silently drops a page.
- **Local mode:** fully automatic PDF generation would need the `offscreen`
  permission, which is not requested. Local mode therefore opens an
  extension-controlled printable page and invokes the browser print dialog
  ("Save as PDF"). This is the documented fallback.

## Modes

| Mode | Network | Account | Cost |
| --- | --- | --- | --- |
| Save this page (default) | **None — fully offline** | Not needed | Free |
| Save this page (`singlePageMode: api`) | `POST /v1/jobs` | API key | 1 credit |
| Build reading pack | `POST /v1/packs` | API key | 1 credit per source + 2 |

## Configuration

Set the **API base URL** and paste an **API key** on the options page. Both ship
empty: the build makes no assumption about where the API lives, and never
assumes `localhost`. Reading packs stay disabled until both are set; local mode
always works.

> CleanPDF has no public sign-up or key-creation endpoint yet, so keys must be
> provisioned server-side (`uv run python -m pdf_api.bootstrap <email>`) and
> handed to the user out of band. See the blocker note in `ORCHESTRATION.md`.

## Store material

`store/listing.md`, `store/privacy.md`, `store/permissions-justification.md`,
`store/screenshot-checklist.md`. No screenshots have been produced — they must
be captured from the real running extension.
