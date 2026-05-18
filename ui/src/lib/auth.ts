/**
 * Auth client for the orchestrator's dev-only token endpoint.
 *
 * In production this module would talk to the corporate IdP. For local
 * dev we hit POST /auth/token with a fixed (sub, role) and cache the
 * resulting JWT in localStorage so every page reuses it.
 */

import axios from 'axios';

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// Default dev identity — change role to "analyst" to test read-only flows
const DEV_SUB = process.env.NEXT_PUBLIC_DEV_SUB || 'adjuster';
const DEV_ROLE = (process.env.NEXT_PUBLIC_DEV_ROLE || 'auditor') as
  | 'analyst'
  | 'auditor'
  | 'admin';

const TOKEN_KEY = 'insureai.token';
const TOKEN_EXPIRES_AT_KEY = 'insureai.token_expires_at';

export interface TokenResponse {
  access_token: string;
  token_type: 'bearer';
  role: 'analyst' | 'auditor' | 'admin';
  expires_in: number; // seconds
}

/** Mint a new JWT against /auth/token. */
async function mintToken(): Promise<TokenResponse> {
  const res = await axios.post<TokenResponse>(`${API_BASE_URL}/auth/token`, {
    sub: DEV_SUB,
    role: DEV_ROLE,
  });
  return res.data;
}

/** Persist a freshly minted token + its absolute expiry. */
function persistToken(t: TokenResponse): void {
  if (typeof window === 'undefined') return;
  const expiresAt = Date.now() + t.expires_in * 1000;
  localStorage.setItem(TOKEN_KEY, t.access_token);
  localStorage.setItem(TOKEN_EXPIRES_AT_KEY, String(expiresAt));
}

/** Return the cached token if it's still good, otherwise null. */
function getCachedToken(): string | null {
  if (typeof window === 'undefined') return null;
  const token = localStorage.getItem(TOKEN_KEY);
  const expiresAt = Number(localStorage.getItem(TOKEN_EXPIRES_AT_KEY) || 0);
  // Treat tokens within 60s of expiry as already gone
  if (!token || Date.now() > expiresAt - 60_000) return null;
  return token;
}

/**
 * Return a usable bearer token — from cache if fresh, otherwise mint one.
 * Throws if the backend is unreachable.
 */
export async function getToken(): Promise<string> {
  const cached = getCachedToken();
  if (cached) return cached;

  const minted = await mintToken();
  persistToken(minted);
  return minted.access_token;
}

/** Force-clear any cached credentials (useful for "log out" buttons). */
export function clearToken(): void {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(TOKEN_EXPIRES_AT_KEY);
}
