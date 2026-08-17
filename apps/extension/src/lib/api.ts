/**
 * CleanPDF API client.
 *
 * The API key is only ever placed in the Authorization header. It is never
 * logged, never put in a URL, and never included in a thrown error message.
 */

import { redactSecrets } from './secrets';
import type { JobResponse, PackItemPayload, UsageResponse } from './types';

export type ApiErrorKind =
  | 'unauthorized' // 401
  | 'payment_required' // 402
  | 'forbidden' // 403
  | 'not_found' // 404
  | 'invalid_request' // 422
  | 'rate_limited' // 429
  | 'unavailable' // 503
  | 'expired' // 410 — signed link no longer valid
  | 'server' // 5xx
  | 'network' // fetch rejected
  | 'not_configured'; // no base URL / no key

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number;
  readonly retryAfter: number | null;

  constructor(kind: ApiErrorKind, message: string, status = 0, retryAfter: number | null = null) {
    super(redactSecrets(message));
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

/** Human-facing copy for each failure mode. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.kind) {
      case 'unauthorized':
        return 'Your API key was rejected. Check it in Options.';
      case 'payment_required':
        return 'Not enough credits for this pack. Top up, then try again.';
      case 'forbidden':
        return 'This request was refused. Verify your account email, then retry.';
      case 'invalid_request':
        return `The request was rejected: ${error.message}`;
      case 'rate_limited':
        return error.retryAfter
          ? `Rate limit reached. Try again in ${error.retryAfter}s.`
          : 'Rate limit reached. Try again shortly.';
      case 'expired':
        return 'That download link has expired. Rebuild the pack to get a fresh one.';
      case 'unavailable':
        return 'The service is temporarily unavailable. Try again shortly.';
      case 'network':
        return 'Could not reach the CleanPDF API. Check your connection and API URL.';
      case 'not_configured':
        return error.message;
      case 'not_found':
        return 'That job no longer exists.';
      default:
        return `Something went wrong (HTTP ${error.status}).`;
    }
  }
  return redactSecrets(error instanceof Error ? error.message : String(error));
}

function kindForStatus(status: number): ApiErrorKind {
  switch (status) {
    case 401:
      return 'unauthorized';
    case 402:
      return 'payment_required';
    case 403:
      return 'forbidden';
    case 404:
      return 'not_found';
    case 410:
      return 'expired';
    case 413:
    case 422:
      return 'invalid_request';
    case 429:
      return 'rate_limited';
    case 503:
      return 'unavailable';
    default:
      return 'server';
  }
}

async function detailOf(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
    return JSON.stringify(body.detail ?? '').slice(0, 300);
  } catch {
    return `HTTP ${response.status}`;
  }
}

export interface ApiConfig {
  baseUrl: string;
  apiKey: string;
}

export function assertConfigured(baseUrl: string, apiKey: string | null): ApiConfig {
  if (!baseUrl) {
    throw new ApiError(
      'not_configured',
      'Set your CleanPDF API URL in Options to use reading packs.',
    );
  }
  if (!apiKey) {
    throw new ApiError('not_configured', 'Add your CleanPDF API key in Options to continue.');
  }
  return { baseUrl: baseUrl.replace(/\/+$/, ''), apiKey };
}

async function request<T>(config: ApiConfig, path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${config.baseUrl}${path}`, {
      ...init,
      headers: {
        ...(init.headers ?? {}),
        // The only place the key ever appears.
        Authorization: `Bearer ${config.apiKey}`,
        'Content-Type': 'application/json',
      },
    });
  } catch {
    // Deliberately does not echo the underlying error, which can contain the URL.
    throw new ApiError('network', 'Network request failed');
  }

  if (!response.ok) {
    const retryAfterHeader = response.headers.get('Retry-After');
    throw new ApiError(
      kindForStatus(response.status),
      await detailOf(response),
      response.status,
      retryAfterHeader ? Number.parseInt(retryAfterHeader, 10) : null,
    );
  }
  return (await response.json()) as T;
}

export interface CreatePackInput {
  items: PackItemPayload[];
  packTitle: string;
  toc: boolean;
  titlePage: boolean;
  marginMm: number;
}

export async function createPack(config: ApiConfig, input: CreatePackInput): Promise<JobResponse> {
  return request<JobResponse>(config, '/v1/packs', {
    method: 'POST',
    body: JSON.stringify({
      items: input.items,
      pack_title: input.packTitle,
      toc: input.toc,
      title_page: input.titlePage,
      margin_mm: input.marginMm,
    }),
  });
}

export interface CreateSingleInput {
  html: string;
  marginMm: number;
}

/** High-fidelity single page conversion through the API. */
export async function createSingleJob(
  config: ApiConfig,
  input: CreateSingleInput,
): Promise<JobResponse> {
  return request<JobResponse>(config, '/v1/jobs', {
    method: 'POST',
    body: JSON.stringify({ html: input.html, margin_mm: input.marginMm }),
  });
}

export async function getJob(config: ApiConfig, jobId: string): Promise<JobResponse> {
  return request<JobResponse>(config, `/v1/jobs/${jobId}`);
}

export async function getUsage(config: ApiConfig): Promise<UsageResponse> {
  return request<UsageResponse>(config, '/v1/usage');
}

export interface PollOptions {
  intervalMs?: number;
  timeoutMs?: number;
  onTick?: (job: JobResponse, elapsedMs: number) => void;
  signal?: AbortSignal;
}

/** Poll the existing job endpoint until the job completes or fails. */
export async function pollJob(
  config: ApiConfig,
  jobId: string,
  options: PollOptions = {},
): Promise<JobResponse> {
  const interval = options.intervalMs ?? 1200;
  const timeout = options.timeoutMs ?? 180_000;
  const started = Date.now();

  for (;;) {
    if (options.signal?.aborted) throw new ApiError('network', 'Cancelled');
    const job = await getJob(config, jobId);
    const elapsed = Date.now() - started;
    options.onTick?.(job, elapsed);

    if (job.status === 'completed' || job.status === 'failed') return job;
    if (elapsed > timeout) {
      throw new ApiError('server', 'Timed out waiting for the pack to finish');
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
}
