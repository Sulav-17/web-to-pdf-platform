import { beforeEach, describe, expect, it, vi } from 'vitest';
import { extractFromTab, extractTabs, isEligibleTab, NEEDS_GRANT_REASON } from '../src/lib/extract';
import type { TabSummary } from '../src/lib/types';

describe('isEligibleTab', () => {
  it.each(['https://example.com/article', 'http://example.com', 'file:///C:/notes.html'])(
    'accepts %s',
    (url) => {
      expect(isEligibleTab(url)).toBe(true);
    },
  );

  it.each([
    'chrome://extensions',
    'chrome-extension://abc/popup.html',
    'edge://settings',
    'about:blank',
    'devtools://devtools/bundled/x.html',
    'view-source:https://example.com',
    'https://chromewebstore.google.com/detail/x',
  ])('rejects %s', (url) => {
    expect(isEligibleTab(url)).toBe(false);
  });

  it('rejects a missing URL', () => {
    expect(isEligibleTab(undefined)).toBe(false);
  });
});

function stubScripting(impl: {
  inject?: () => Promise<unknown>;
  call?: () => Promise<unknown>;
}): void {
  let calls = 0;
  vi.stubGlobal('chrome', {
    scripting: {
      executeScript: vi.fn(async () => {
        calls += 1;
        if (calls === 1) return (await impl.inject?.()) ?? [{ result: undefined }];
        return (await impl.call?.()) ?? [{ result: undefined }];
      }),
    },
  });
}

describe('extractFromTab', () => {
  beforeEach(() => vi.unstubAllGlobals());

  it('returns the extractor result on success', async () => {
    stubScripting({
      call: async () => [{ result: { ok: true, title: 'T', html: '<p>x</p>' } }],
    });
    await expect(extractFromTab(1, true)).resolves.toEqual({
      ok: true,
      title: 'T',
      html: '<p>x</p>',
    });
  });

  it('explains how to grant access when injection is refused', async () => {
    stubScripting({
      inject: async () => {
        throw new Error('Cannot access contents of the page');
      },
    });
    await expect(extractFromTab(1, true)).resolves.toEqual({
      ok: false,
      reason: NEEDS_GRANT_REASON,
    });
  });

  it('reports a failure rather than throwing when the call fails', async () => {
    stubScripting({
      call: async () => {
        throw new Error('frame was removed');
      },
    });
    const outcome = await extractFromTab(1, true);
    expect(outcome.ok).toBe(false);
  });

  it('handles an extractor that returns nothing', async () => {
    stubScripting({ call: async () => [] });
    const outcome = await extractFromTab(1, true);
    expect(outcome).toEqual({ ok: false, reason: 'No response from this tab.' });
  });
});

describe('extractTabs', () => {
  beforeEach(() => vi.unstubAllGlobals());

  const tabs: TabSummary[] = [
    { id: 1, title: 'One', url: 'https://a.test' },
    { id: 2, title: 'Two', url: 'https://b.test' },
    { id: 3, title: 'Three', url: 'https://c.test' },
  ];

  it('preserves the given order and keeps per-tab failures isolated', async () => {
    vi.stubGlobal('chrome', {
      scripting: {
        executeScript: vi.fn(async (args: { target: { tabId: number }; files?: string[] }) => {
          if (args.files) {
            if (args.target.tabId === 2) throw new Error('Cannot access contents of the page');
            return [{ result: undefined }];
          }
          return [{ result: { ok: true, title: `T${args.target.tabId}`, html: '<p>x</p>' } }];
        }),
      },
    });

    const attempts = await extractTabs(tabs, true);
    expect(attempts.map((a) => a.tab.id)).toEqual([1, 2, 3]);
    expect(attempts.map((a) => a.outcome.ok)).toEqual([true, false, true]);
    // The failing tab does not corrupt its neighbours.
    expect(attempts[0]?.outcome).toMatchObject({ ok: true, title: 'T1' });
    expect(attempts[2]?.outcome).toMatchObject({ ok: true, title: 'T3' });
  });

  it('reports progress for every tab', async () => {
    vi.stubGlobal('chrome', {
      scripting: {
        executeScript: vi.fn(async () => [{ result: { ok: true, title: 'T', html: '<p>x</p>' } }]),
      },
    });
    const seen: number[] = [];
    await extractTabs(tabs, true, (done) => seen.push(done));
    expect(seen).toEqual([0, 1, 2, 3]);
  });
});
