/**
 * API key storage.
 *
 * The key lives in chrome.storage.local, never chrome.storage.sync, so it is
 * not replicated to other devices through the user's Google account. It is
 * never written to the DOM, never logged, and never included in error text —
 * callers display `maskApiKey()` instead.
 */

const KEY = 'apiKey';

export async function getApiKey(): Promise<string | null> {
  const stored = await chrome.storage.local.get(KEY);
  const value = stored[KEY];
  return typeof value === 'string' && value.length > 0 ? value : null;
}

export async function setApiKey(raw: string): Promise<void> {
  const value = raw.trim();
  if (!value) throw new Error('API key must not be empty');
  await chrome.storage.local.set({ [KEY]: value });
}

export async function clearApiKey(): Promise<void> {
  await chrome.storage.local.remove(KEY);
}

export async function hasApiKey(): Promise<boolean> {
  return (await getApiKey()) !== null;
}

/**
 * Render a key for display: prefix plus the last four characters only.
 * Never returns enough material to reconstruct the key.
 */
export function maskApiKey(raw: string): string {
  const tail = raw.slice(-4);
  return `••••••••${tail}`;
}

/**
 * Defence in depth: strip anything that looks like a key from text that might
 * be surfaced to the user or a log sink.
 */
export function redactSecrets(text: string): string {
  return text.replace(/sk_[A-Za-z0-9_-]{8,}/g, 'sk_***redacted***');
}
