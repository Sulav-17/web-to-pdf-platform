/**
 * Hands a cleaned document from the popup to the extension's print page.
 *
 * Uses chrome.storage.session, which is held in memory only, is never written
 * to disk, and is cleared when the browser closes. The entry is removed the
 * moment the print page reads it, so page content is transient.
 */

import type { Settings } from './settings';

export interface PrintJob {
  title: string;
  html: string;
  sourceUrl: string;
  filename: string;
  settings: Settings;
}

const PREFIX = 'print:';

function newKey(): string {
  return PREFIX + crypto.randomUUID();
}

export async function stashPrintJob(job: PrintJob): Promise<string> {
  const key = newKey();
  await chrome.storage.session.set({ [key]: job });
  return key;
}

/** Read once and delete. Returns null if the key is unknown or already used. */
export async function takePrintJob(key: string): Promise<PrintJob | null> {
  if (!key.startsWith(PREFIX)) return null;
  const stored = await chrome.storage.session.get(key);
  const job = stored[key] as PrintJob | undefined;
  await chrome.storage.session.remove(key);
  return job ?? null;
}
