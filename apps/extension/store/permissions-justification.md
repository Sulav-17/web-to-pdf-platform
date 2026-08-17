# Permissions justification

CleanPDF requests four permissions: `activeTab`, `scripting`, `tabs` and
`storage`. It requests **no host permissions**, so it has no standing access to
any website.

## `tabs` and `scripting` (the paragraph for the store reviewer)

CleanPDF's purpose is to turn the articles you are already reading into a single
clean PDF, so it must be able to (a) show you which of your open tabs are
candidates and (b) read the article text out of the ones you pick. The `tabs`
permission is used for exactly one thing: listing the title and URL of the tabs
in your current window so the popup can present them as a checklist you can tick
and drag into order. It is never used to observe navigation, build history, or
report anything anywhere. The `scripting` permission is used for exactly one
thing: running the bundled Mozilla Readability extractor inside a tab **you have
ticked**, at the moment you press "Build reading pack", to pull out the article
title and body and strip scripts, handlers, and page furniture. Extraction runs
only on demand, only on tabs you selected, and the result goes only where you
send it — your local print dialog, or the CleanPDF API for a pack you asked for.
Neither permission is used in the background: the extension has no content
scripts, no background polling, and no listeners on navigation.

## `activeTab`

Grants temporary access to the tab you are on when you click the CleanPDF
toolbar icon. This is what powers the free, offline "Save this page as a clean
PDF" action, and it is why CleanPDF does not need host permissions.

## `storage`

Persists your preferences (font size, margins, include-images, filename source,
API base URL) in `chrome.storage.sync`, your API key in `chrome.storage.local`
(device-only, never synced), and one in-flight print job in
`chrome.storage.session` (memory-only, deleted on read).

## Permissions deliberately NOT requested

| Not requested | Why it was avoided |
| --- | --- |
| `host_permissions` / `<all_urls>` | Standing access to every site is not needed; `activeTab` plus explicit per-tab invocation covers the use case. |
| `offscreen` | Would allow silent background PDF generation. CleanPDF uses the browser print dialog instead — see the limitation below. |
| `downloads` | The print dialog and normal tab navigation already deliver the file. |
| `history`, `cookies`, `webRequest`, `webNavigation` | Never needed; would represent exactly the surveillance the product avoids. |

## Known limitation, stated honestly

Because CleanPDF holds **no host permissions**, Chrome only grants it script
access to a tab through `activeTab` — that is, to a tab where **you have invoked
the extension**. Two consequences follow, and both are surfaced in the UI rather
than hidden:

1. **Multi-tab packs need a one-time grant per tab.** To include a tab in a
   reading pack, switch to it and click the CleanPDF icon once. The popup marks
   any tab it cannot yet read and tells you exactly this; it never silently drops
   a page from your pack.
2. **Local mode uses the print dialog, not automatic PDF generation.** Producing
   a PDF file without any user interaction would require the `offscreen`
   permission (or host permissions). CleanPDF does not request either, so local
   mode opens an extension-controlled printable page and invokes the browser's
   print dialog, where you choose "Save as PDF". This is the documented fallback
   and is a deliberate trade of convenience for a much smaller permission
   surface.
