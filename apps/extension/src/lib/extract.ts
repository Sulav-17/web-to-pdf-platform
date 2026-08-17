/**
 * Drives per-tab extraction from the popup.
 *
 * Permission note: with only `activeTab` (no host permissions) Chrome grants
 * script access to a tab when the user invokes the extension *on that tab*.
 * Tabs that have never been granted therefore fail here with a clear,
 * actionable reason instead of silently dropping out of the pack.
 */

import type { ExtractOutcome, ItemAttempt, TabSummary } from './types';

const INJECT_FILE = 'inject/extract.js';

const BLOCKED_SCHEMES = [
  'chrome:',
  'chrome-extension:',
  'edge:',
  'about:',
  'devtools:',
  'view-source:',
  'chrome-search:',
  'chrome-untrusted:',
];

/** Pages the extension can never script, so they are never offered. */
export function isEligibleTab(url: string | undefined): boolean {
  if (!url) return false;
  const lower = url.toLowerCase();
  if (BLOCKED_SCHEMES.some((scheme) => lower.startsWith(scheme))) return false;
  if (lower.startsWith('https://chrome.google.com/webstore')) return false;
  if (lower.startsWith('https://chromewebstore.google.com')) return false;
  return lower.startsWith('http:') || lower.startsWith('https:') || lower.startsWith('file:');
}

export const NEEDS_GRANT_REASON =
  'CleanPDF has no access to this tab yet. Switch to it, click the CleanPDF icon once, then come back.';

export async function listEligibleTabs(): Promise<TabSummary[]> {
  const tabs = await chrome.tabs.query({ currentWindow: true });
  return tabs
    .filter((tab) => typeof tab.id === 'number' && isEligibleTab(tab.url))
    .map((tab) => ({
      id: tab.id as number,
      title: tab.title?.trim() || tab.url || 'Untitled',
      url: tab.url ?? '',
    }));
}

/** Extract one tab. Never throws: failures come back as `{ ok: false }`. */
export async function extractFromTab(
  tabId: number,
  includeImages: boolean,
): Promise<ExtractOutcome> {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: [INJECT_FILE] });
  } catch {
    // Almost always "cannot access contents of the page" — i.e. no activeTab grant.
    return { ok: false, reason: NEEDS_GRANT_REASON };
  }

  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: (options: { includeImages: boolean }) =>
        window.__cleanpdfExtract?.(options) ?? {
          ok: false as const,
          reason: 'Extractor did not load on this page.',
        },
      args: [{ includeImages }],
    });
    const outcome = results[0]?.result as ExtractOutcome | undefined;
    return outcome ?? { ok: false, reason: 'No response from this tab.' };
  } catch (error) {
    return {
      ok: false,
      reason: error instanceof Error ? error.message : 'Extraction failed on this tab.',
    };
  }
}

/**
 * Extract several tabs, preserving the caller's order exactly.
 * Runs sequentially so progress is reportable and the browser is not hammered.
 */
export async function extractTabs(
  tabs: TabSummary[],
  includeImages: boolean,
  onProgress?: (done: number, total: number, tab: TabSummary) => void,
): Promise<ItemAttempt[]> {
  const attempts: ItemAttempt[] = [];
  for (const [index, tab] of tabs.entries()) {
    onProgress?.(index, tabs.length, tab);
    attempts.push({ tab, outcome: await extractFromTab(tab.id, includeImages) });
  }
  onProgress?.(tabs.length, tabs.length, tabs[tabs.length - 1] ?? tabs[0] ?? ({} as TabSummary));
  return attempts;
}

export function succeeded(attempts: ItemAttempt[]): ItemAttempt[] {
  return attempts.filter((attempt) => attempt.outcome.ok);
}

export function failed(attempts: ItemAttempt[]): ItemAttempt[] {
  return attempts.filter((attempt) => !attempt.outcome.ok);
}
