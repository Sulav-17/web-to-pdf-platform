# Manual screenshot capture checklist

**No screenshots have been produced.** Chrome Web Store screenshots must come
from the real, running extension — do not mock, composite, or generate them.
Work through this list against a loaded unpacked build and capture as you go.

## Before you start

- [ ] Build the extension: `npm run dist` in `apps/extension`.
- [ ] Load `apps/extension/dist` at `chrome://extensions` (Developer mode → Load unpacked).
- [ ] Use a **fresh Chrome profile** so no unrelated bookmarks, extensions or
      accounts appear in frame.
- [ ] Set the display to 100% zoom; capture at 1280×800 (Chrome Web Store accepts
      1280×800 or 640×400).
- [ ] Open only tabs whose content you are willing to publish — the tab titles
      and hostnames will be visible.
- [ ] Confirm the options page shows a **masked** key (`••••••••1234`) and never
      the raw key. If a raw key is visible anywhere, retake the shot.

## Required screenshots (5)

| # | Shot | What must be visible | Notes |
| --- | --- | --- | --- |
| 1 | Popup with tab list | Several eligible tabs, checkboxes, drag grips, "Build reading pack" and "Save this page" buttons, credit balance | The hero image. Tick 4–5 tabs. |
| 2 | Drag-to-order in progress | A row mid-drag with the drop indicator visible | Capture during the drag; may need a timed screenshot. |
| 3 | Progress + completion | Status area showing "Building your pack…" or "Your pack is ready" with the "Open PDF" action | Shows the flow completes. |
| 4 | Resulting PDF | The merged PDF open in a viewer with the **bookmarks/outline pane expanded** and the contents page visible | Proves bookmarks and TOC are real. |
| 5 | Options page | Font size, margins, include images, filename source, and the **masked** key state | Do not show the API base URL if it is a private host. |

## Optional extras

- [ ] Local mode print dialog with "Save as PDF" selected as the destination
      (demonstrates the offline path).
- [ ] Side-by-side of a cluttered source page and the cleaned PDF.

## Promotional tile (optional but recommended)

- [ ] 440×280 small tile. Use the wordmark plus the tagline
      "Turn your open tabs into one clean, bookmarked PDF."

## Final review before upload

- [ ] No API keys, tokens, or `Authorization` headers visible in any frame.
- [ ] No personal email addresses, account names, or avatars.
- [ ] No paywalled or copyrighted article body shown in full.
- [ ] No `localhost` URLs in the address bar or options page.
- [ ] No DevTools panels or extension-error badges in frame.
- [ ] Each image is exactly 1280×800 or 640×400, PNG or JPEG, under 5 MB.
