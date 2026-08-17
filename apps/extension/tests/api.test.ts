import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  assertConfigured,
  createPack,
  describeError,
  getUsage,
  pollJob,
} from '../src/lib/api';

const KEY = 'sk_live_supersecretvalue123456';
const config = { baseUrl: 'https://api.example.com', apiKey: KEY };

function mockFetch(status: number, body: unknown, headers: Record<string, string> = {}) {
  const fn = vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json', ...headers },
      }),
  );
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe('assertConfigured', () => {
  it('requires a base URL', () => {
    expect(() => assertConfigured('', KEY)).toThrow(ApiError);
  });

  it('requires a key', () => {
    expect(() => assertConfigured('https://api.example.com', null)).toThrow(ApiError);
  });

  it('trims trailing slashes', () => {
    expect(assertConfigured('https://api.example.com//', KEY).baseUrl).toBe(
      'https://api.example.com',
    );
  });
});

describe('createPack', () => {
  it('sends items in the given order with the contract field names', async () => {
    const fetchMock = mockFetch(202, { id: 'job-1', status: 'queued' });
    await createPack(config, {
      items: [
        { html: '<p>1</p>', title: 'First' },
        { html: '<p>2</p>', title: 'Second' },
      ],
      packTitle: 'Weekly',
      toc: true,
      titlePage: false,
      marginMm: 12,
    });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://api.example.com/v1/packs');
    const body = JSON.parse(init.body as string);
    expect(body.items.map((i: { title: string }) => i.title)).toEqual(['First', 'Second']);
    expect(body.pack_title).toBe('Weekly');
    expect(body.toc).toBe(true);
    expect(body.title_page).toBe(false);
  });

  it('sends the key only in the Authorization header, never the URL', async () => {
    const fetchMock = mockFetch(202, { id: 'job-1' });
    await createPack(config, {
      items: [{ html: '<p>1</p>', title: 'T' }],
      packTitle: 'P',
      toc: true,
      titlePage: true,
      marginMm: 12,
    });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).not.toContain(KEY);
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${KEY}`);
  });
});

describe('error mapping', () => {
  it.each([
    [401, 'unauthorized'],
    [402, 'payment_required'],
    [403, 'forbidden'],
    [404, 'not_found'],
    [410, 'expired'],
    [422, 'invalid_request'],
    [429, 'rate_limited'],
    [503, 'unavailable'],
    [500, 'server'],
  ])('maps HTTP %i to %s', async (status, kind) => {
    mockFetch(status, { detail: 'nope' });
    await expect(getUsage(config)).rejects.toMatchObject({ kind });
  });

  it('captures Retry-After for rate limits', async () => {
    mockFetch(429, { detail: 'slow down' }, { 'Retry-After': '30' });
    await expect(getUsage(config)).rejects.toMatchObject({ kind: 'rate_limited', retryAfter: 30 });
  });

  it('reports network failures without leaking the request', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error(`connect ECONNREFUSED for ${KEY}`);
      }),
    );
    const error = await getUsage(config).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).kind).toBe('network');
    expect((error as ApiError).message).not.toContain(KEY);
  });

  it('redacts keys that appear in server error text', () => {
    const error = new ApiError('server', `boom for ${KEY}`, 500);
    expect(error.message).not.toContain(KEY);
    expect(error.message).toContain('redacted');
  });

  it('gives actionable copy for each failure', () => {
    expect(describeError(new ApiError('payment_required', 'x', 402))).toMatch(/credits/i);
    expect(describeError(new ApiError('unauthorized', 'x', 401))).toMatch(/key/i);
    expect(describeError(new ApiError('expired', 'x', 410))).toMatch(/expired/i);
    expect(describeError(new ApiError('rate_limited', 'x', 429, 12))).toContain('12s');
  });

  it('never echoes a key through describeError', () => {
    expect(describeError(new Error(`failed with ${KEY}`))).not.toContain(KEY);
  });
});

describe('pollJob', () => {
  it('returns once the job completes', async () => {
    const responses = [
      { id: 'j', status: 'queued', download_url: null },
      { id: 'j', status: 'rendering', download_url: null },
      { id: 'j', status: 'completed', download_url: 'https://api.example.com/dl' },
    ];
    let call = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        const body = responses[Math.min(call++, responses.length - 1)];
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );

    const ticks: string[] = [];
    const job = await pollJob(config, 'j', {
      intervalMs: 1,
      onTick: (current) => ticks.push(current.status),
    });
    expect(job.status).toBe('completed');
    expect(job.download_url).toBe('https://api.example.com/dl');
    expect(ticks).toEqual(['queued', 'rendering', 'completed']);
  });

  it('returns failed jobs rather than throwing', async () => {
    mockFetch(200, { id: 'j', status: 'failed', failure_reason: 'render_error: boom' });
    const job = await pollJob(config, 'j', { intervalMs: 1 });
    expect(job.status).toBe('failed');
    expect(job.failure_reason).toContain('render_error');
  });

  it('times out instead of polling forever', async () => {
    mockFetch(200, { id: 'j', status: 'queued' });
    await expect(pollJob(config, 'j', { intervalMs: 1, timeoutMs: 5 })).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});
