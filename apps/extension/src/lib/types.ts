export interface TabSummary {
  id: number;
  title: string;
  url: string;
}

/** Result of running the extractor inside one tab. */
export type ExtractOutcome =
  | { ok: true; title: string; html: string }
  | { ok: false; reason: string };

/** A per-tab extraction attempt, kept alongside the tab it came from. */
export interface ItemAttempt {
  tab: TabSummary;
  outcome: ExtractOutcome;
}

export interface PackItemPayload {
  html: string;
  title: string;
}

export interface JobResponse {
  id: string;
  kind: string;
  status: 'queued' | 'rendering' | 'completed' | 'failed';
  credits_charged: number;
  output_bytes: number | null;
  failure_reason: string | null;
  download_url: string | null;
}

export interface UsageResponse {
  balance: number;
  plan_id: string | null;
  monthly_credits: number | null;
}
