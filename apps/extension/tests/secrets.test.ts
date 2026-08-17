import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  clearApiKey,
  getApiKey,
  hasApiKey,
  maskApiKey,
  redactSecrets,
  setApiKey,
} from '../src/lib/secrets';

const KEY = 'sk_live_abcdefghijklmnop1234';

/** Minimal in-memory chrome.storage double, tracking which area was used. */
function stubStorage() {
  const local: Record<string, unknown> = {};
  const sync: Record<string, unknown> = {};
  vi.stubGlobal('chrome', {
    storage: {
      local: {
        get: vi.fn(async (k: string) => (k in local ? { [k]: local[k] } : {})),
        set: vi.fn(async (items: Record<string, unknown>) => Object.assign(local, items)),
        remove: vi.fn(async (k: string) => {
          delete local[k];
        }),
      },
      sync: {
        get: vi.fn(async () => ({})),
        set: vi.fn(async (items: Record<string, unknown>) => Object.assign(sync, items)),
      },
    },
  });
  return { local, sync };
}

describe('api key storage', () => {
  beforeEach(() => vi.unstubAllGlobals());

  it('round-trips a key', async () => {
    stubStorage();
    await setApiKey(KEY);
    expect(await getApiKey()).toBe(KEY);
    expect(await hasApiKey()).toBe(true);
  });

  it('stores the key in local storage, never in synced storage', async () => {
    const areas = stubStorage();
    await setApiKey(KEY);
    expect(Object.values(areas.local)).toContain(KEY);
    expect(JSON.stringify(areas.sync)).not.toContain(KEY);
  });

  it('removes the key', async () => {
    stubStorage();
    await setApiKey(KEY);
    await clearApiKey();
    expect(await getApiKey()).toBeNull();
    expect(await hasApiKey()).toBe(false);
  });

  it('rejects an empty key', async () => {
    stubStorage();
    await expect(setApiKey('   ')).rejects.toThrow();
  });

  it('returns null when nothing is stored', async () => {
    stubStorage();
    expect(await getApiKey()).toBeNull();
  });
});

describe('maskApiKey', () => {
  it('reveals only the last four characters', () => {
    const masked = maskApiKey(KEY);
    expect(masked).toBe('••••••••1234');
    expect(masked).not.toContain('sk_live');
    expect(masked.length).toBeLessThan(KEY.length);
  });
});

describe('redactSecrets', () => {
  it('removes anything key-shaped from text', () => {
    const text = redactSecrets(`request failed for ${KEY} at 10:00`);
    expect(text).not.toContain(KEY);
    expect(text).toContain('redacted');
  });

  it('leaves ordinary text alone', () => {
    expect(redactSecrets('plain message')).toBe('plain message');
  });
});
