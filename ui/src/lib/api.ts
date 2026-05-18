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
  status: string;
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

export async function submitClaim(pdf: File): Promise<ClaimSubmitResponse> {
  const fd = new FormData();
  fd.append('pdf', pdf);
  // Override Content-Type so axios sets the multipart boundary.
  // Auth header attached automatically by the request interceptor.
  const res = await api.post<ClaimSubmitResponse>(
    '/claims/submit-upload',
    fd,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
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
