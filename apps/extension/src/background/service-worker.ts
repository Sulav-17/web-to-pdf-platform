/**
 * MV3 service worker.
 *
 * Deliberately minimal. It holds no page content, makes no network calls, and
 * sends no analytics (see store/privacy.md for the reasoning).
 */

chrome.runtime.onInstalled.addListener((details) => {
  if (details.reason === 'install') {
    // First run: show Options so the user can set up local mode preferences
    // and, optionally, API access.
    chrome.runtime.openOptionsPage().catch(() => {
      /* opening options is best-effort */
    });
  }
});
