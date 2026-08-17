# Privacy explanation

## The short version

CleanPDF processes page content **transiently** and **never stores it**. Local
single-page mode is **fully offline** and makes no network call at all.

## What happens to page content

**Local mode ("Save this page as a clean PDF") — fully offline.**
The article is extracted inside the tab, cleaned, handed to an
extension-controlled print page through in-memory session storage, and rendered
by your browser's own print dialog. The entry is deleted the moment the print
page reads it. Nothing is uploaded, and no network request is made. This mode
needs no account and no API key.

**Reading packs — only what you tick.**
When you build a reading pack, CleanPDF sends the cleaned article HTML and the
title of the tabs **you explicitly selected** to the CleanPDF API so it can
render and merge them. It sends nothing else. Tabs you did not tick are never
read. Your browsing history is never read or transmitted. The API keeps the
finished PDF only until its expiry window elapses, and CleanPDF itself stores
none of it.

## What the extension stores

| Stored | Where | Why |
| --- | --- | --- |
| Font size, margins, include-images, filename source, single-page mode, API base URL | `chrome.storage.sync` | Your preferences, so they follow your Chrome profile |
| API key | `chrome.storage.local` | Kept on this device only — deliberately **not** synced |
| One in-flight print job | `chrome.storage.session` | Memory-only; deleted as soon as the print page reads it |

No page content, URL, title or PDF is ever written to `chrome.storage.sync` or
`chrome.storage.local`.

## API keys

Your API key is stored in local extension storage, sent only in the
`Authorization` header of requests to the API base URL you configured, and never
written to a URL, a log, the page, or an error message. The options page shows
only a masked hint (`••••••••1234`) and never reads the stored key back into the
form. Error text is passed through a redaction filter before it is displayed.

## Analytics

**CleanPDF ships with no analytics of any kind.** No events, no page views, no
identifiers. The project's existing PostHog wrapper is a server-side helper and
is not a privacy-reviewed client for extension use, so rather than add telemetry
merely to satisfy an optional instrumentation item, none was added. If analytics
are introduced later, they must never carry URLs, titles, HTML, API keys,
filenames or browsing data.

## Remote code

The extension executes no remotely hosted code. Mozilla Readability is bundled
into the shipped package at build time. The extension pages run under a
`script-src 'self'` Content Security Policy, and the production build is audited
(`npm run audit`) to reject `eval`, `new Function`, `importScripts`,
`document.write` and remote `<script src>`.

## Data deletion

Remove the API key from the options page, or uninstall the extension, and
nothing of yours remains in the browser.
