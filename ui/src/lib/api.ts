/**
 * HTTP client for the orchestrator API.
 *
 * Every request is automatically wrapped with a Bearer JWT obtained
 * from the auth module. Types here mirror packages/common/schemas/api.py
 * exactly — keep them in sync when the backend evolves.
 */

import axios, { AxiosError, AxiosInstance, InternalAxiosRequestConfig } from 'axios';
import { clearToken, getToken } from './auth';

/**
 * NOTE on error handling: every function below lets the raw `AxiosError`
 * propagate (no `try`/`catch`). Components convert it to a localised
 * snackbar message via `toFriendlyError()` in `lib/errors.ts`. This keeps
 * a single normalisation layer and preserves the original response.
 */

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// ── Types — must match packages/common/schemas/api.py ────────
export type SeverityLabel = 'LOW' | 'HIGH' | 'TOTAL_LOSS';
export type ClaimStatus =
  | 'PENDING'
  | 'MANUAL_REVIEW'
  | 'APPROVED'
  | 'REJECTED'
  | 'AWAITING_INPUT';
export type PipelineStatus = 'SUCCESS' | 'ANALYST_FAILED' | 'AUDITOR_FAILED';

/** Mirrors ClaimStatusResponse on the backend. */
export interface Claim {
  claim_id: number;
  user_id: number;
  policy_id: number;
  incident_date: string;
  incident_description: string;
  incident_location: string | null;
  severity_label: SeverityLabel | string;
  reported_damage_amount: string | null;
  claim_status: ClaimStatus | string;
  ai_reasoning: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface ClaimListResponse {
  claims: Claim[];
  total: number;
  limit: number;
  offset: number;
}

/** Mirrors UserResponse on the backend. */
export interface User {
  user_id: number;
  name: string;
  email: string;
}

/** Mirrors PolicyResponse on the backend. */
export interface Policy {
  policy_id: number;
  user_id: number;
  type: string;
  /** Effective status: ACTIVE rows past their end_date are reported as EXPIRED. */
  status: string;
  /** Raw status as stored in the DB — set when it differs from `status`. */
  stored_status?: string | null;
  deductible_usd: string;
  coverage_limit_usd: string;
  start_date: string;
  end_date: string;
}

export interface PolicyListResponse {
  user_id: number;
  policies: Policy[];
  total: number;
}

/** Mirrors TraceObservationItem on the backend (D5.1). */
export interface TraceObservation {
  id: string;
  parent_id: string | null;
  name: string | null;
  type: 'SPAN' | 'GENERATION' | 'EVENT' | string | null;
  started_at: string | null;
  ended_at: string | null;
  latency_ms: number | null;
  cost_usd: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  level: 'DEFAULT' | 'DEBUG' | 'WARNING' | 'ERROR' | string | null;
  status_message: string | null;
}

/** Mirrors ClaimTraceResponse on the backend (D5.1). */
export interface ClaimTrace {
  claim_id: number;
  trace_id: string;
  name: string | null;
  total_latency_ms: number | null;
  total_cost_usd: string | null;
  started_at: string | null;
  ended_at: string | null;
  langfuse_url: string | null;
  observations: TraceObservation[];
}

export interface ClaimSubmitResponse {
  request_id: string;
  timestamp: string;
  pipeline_status: PipelineStatus;
  recommended_decision: string | null;
  claim_status: string | null;
  persisted_claim_id: number | null;
  summary: string | null;
  triage_report: Record<string, unknown> | null;
  claim_decision: Record<string, unknown> | null;
  analyst_retries: number;
  auditor_retries: number;
  analyst_latency_ms: number;
  auditor_latency_ms: number;
  total_latency_ms: number;
  error_message: string | null;
}

// ── Axios instance with auto-auth ────────────────────────────
const api: AxiosInstance = axios.create({ baseURL: API_BASE_URL });

api.interceptors.request.use(
  async (config: InternalAxiosRequestConfig) => {
    // Allow the auth endpoint itself through unauthenticated.
    if (config.url?.startsWith('/auth/')) return config;
    try {
      const token = await getToken();
      config.headers = config.headers ?? {};
      (config.headers as Record<string, string>).Authorization = `Bearer ${token}`;
    } catch {
      // If we can't get a token, let the request go and fail loudly.
    }
    return config;
  },
);

// Drop the cached token on 401 so the next call mints a fresh one.
api.interceptors.response.use(
  (res) => res,
  (err: AxiosError) => {
    if (err.response?.status === 401) clearToken();
    return Promise.reject(err);
  },
);

// ── API surface ──────────────────────────────────────────────

export async function getClaims(
  status?: ClaimStatus,
  limit = 100,
  offset = 0,
): Promise<Claim[]> {
  const params: Record<string, string | number> = { limit, offset };
  if (status) params.status = status;
  const res = await api.get<ClaimListResponse>('/claims', { params });
  return res.data.claims;
}

export async function getClaimDetail(claimId: number): Promise<Claim> {
  const res = await api.get<Claim>(`/claims/${claimId}`);
  return res.data;
}

export async function submitClaim(
  pdf:     File,
  traceId?: string,
): Promise<ClaimSubmitResponse> {
  const fd = new FormData();
  fd.append('pdf', pdf);
  // Override Content-Type so axios sets the multipart boundary.
  // Auth header attached automatically by the request interceptor.
  // When `traceId` is passed, it overrides the middleware-generated id —
  // lets the UI open `/claims/stream/{traceId}` in parallel and correlate
  // events with this exact submission.
  const headers: Record<string, string> = {
    'Content-Type': 'multipart/form-data',
  };
  if (traceId) headers['X-Trace-Id'] = traceId;
  const res = await api.post<ClaimSubmitResponse>(
    '/claims/submit-upload',
    fd,
    {
      headers,
      // PDF processing can be slow when the LLM is cold
      timeout: 120_000,
    },
  );
  return res.data;
}

export async function approveClaim(claimId: number, reason: string): Promise<Claim> {
  const res = await api.post<Claim>(`/claims/${claimId}/approve`, { reason });
  return res.data;
}

export async function rejectClaim(claimId: number, reason: string): Promise<Claim> {
  const res = await api.post<Claim>(`/claims/${claimId}/reject`, { reason });
  return res.data;
}

export async function reanalyzeClaim(claimId: number): Promise<Claim> {
  // Backend re-runs the agents against the stored PDF in advisory mode
  // (no new row) and appends the fresh recommendation to ai_reasoning.
  //
  // May throw a 409 AxiosError with `detail.error === 'pdf_missing'` —
  // callers should catch that and offer to re-upload via
  // `reanalyzeClaimWithUpload`.
  const res = await api.post<Claim>(`/claims/${claimId}/reanalyze`, {}, {
    // The agent pipeline is slow; give it the same headroom as submit.
    timeout: 120_000,
  });
  return res.data;
}

export async function reanalyzeClaimWithUpload(
  claimId: number,
  pdf: File,
): Promise<Claim> {
  const fd = new FormData();
  fd.append('pdf', pdf);
  const res = await api.post<Claim>(
    `/claims/${claimId}/reanalyze-upload`,
    fd,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120_000,
    },
  );
  return res.data;
}

/** True if `err` is an AxiosError signalling the PDF needs re-uploading. */
export function isPdfMissingError(err: unknown): boolean {
  if (!axios.isAxiosError(err)) return false;
  if (err.response?.status !== 409) return false;
  const detail = (err.response.data as { detail?: unknown })?.detail;
  if (detail && typeof detail === 'object') {
    return (detail as { error?: string }).error === 'pdf_missing';
  }
  return false;
}

export async function getClaimTrace(claimId: number): Promise<ClaimTrace> {
  // Proxies Langfuse via the orchestrator so the browser never sees
  // the Langfuse secret key. Returns the flat observations list +
  // deep-link URL; component builds the tree client-side.
  const res = await api.get<ClaimTrace>(`/claims/${claimId}/trace`);
  return res.data;
}

export async function getUser(userId: number): Promise<User> {
  const res = await api.get<User>(`/users/${userId}`);
  return res.data;
}

export async function getUserPolicies(userId: number): Promise<Policy[]> {
  const res = await api.get<PolicyListResponse>(`/users/${userId}/policies`);
  return res.data.policies;
}

export async function getHealth(): Promise<{ status: string }> {
  const res = await api.get('/health');
  return res.data;
}

// Silence the unused-import warning when `AxiosError` isn't referenced
// in this file (it's only here to preserve the type re-export for callers).
export type { AxiosError };
