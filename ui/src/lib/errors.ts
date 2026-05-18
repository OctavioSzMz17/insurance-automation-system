/**
 * Normalise errors from the orchestrator into friendly, localised
 * messages we can surface in a snackbar.
 *
 * The orchestrator returns three shapes:
 *
 * 1. **Validation (422)** — structured violations:
 *      { detail: { error: "Input validation failed",
 *                  violations: [{ field, rule, actual_value,
 *                                 expected_constraint, severity }] } }
 *
 * 2. **Rate limit (429)** — same shape + `Retry-After` header.
 *
 * 3. **Plain (400/404/409/500)** — `{ detail: "string" }` or `{ detail: object }`.
 *
 * To stay framework-agnostic, this module takes a translator `t` (the
 * function returned by `useTranslations()`) plus an optional `tStatus`
 * for the status-title lookup. Components wire those in once and pass
 * the resulting `FriendlyError` to the snackbar.
 */

import axios, { AxiosError } from 'axios';

export type ErrorTone = 'error' | 'warning';

export interface FriendlyError {
  title: string;
  message: string;
  tone: ErrorTone;
  /** Seconds to wait before retrying — set on 429s. */
  retryAfter?: number;
}

interface Violation {
  field?: string;
  rule?: string;
  actual_value?: string;
  expected_constraint?: string;
  severity?: 'error' | 'warning';
}

/**
 * Minimal translator contract — matches what `useTranslations()` returns.
 * We only need positional lookups with `{var}` substitution.
 */
export type TranslatorFn = (
  key: string,
  values?: Record<string, string | number>,
) => string;

interface ErrorTranslators {
  /** Scoped to `errors` namespace. */
  t: TranslatorFn;
  /** Optional fallback when a status code has no specific title. */
  tCommon?: TranslatorFn;
}

function renderViolation(v: Violation, { t }: ErrorTranslators): string {
  if (v.rule) {
    const key = `rules.${v.rule}`;
    const translated = t(key);
    // next-intl returns the key on a miss — detect that and fall back.
    if (translated && translated !== key) return translated;
  }
  if (v.expected_constraint) {
    return v.field
      ? t('fieldConstraint', { field: v.field, constraint: v.expected_constraint })
      : v.expected_constraint;
  }
  return t('unspecified');
}

function extractRetryAfter(err: AxiosError): number | undefined {
  const raw = err.response?.headers?.['retry-after'];
  if (!raw) return undefined;
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : undefined;
}

function statusTitle(status: number, t: TranslatorFn): string {
  const key = `statusTitles.${status}`;
  const translated = t(key);
  if (translated && translated !== key) return translated;
  if (status >= 500) return t('statusTitles.500');
  return t('generic.title');
}

/**
 * Convert any thrown value into a localised `FriendlyError`. Never throws.
 *
 * @example
 *   const t = useTranslations('errors');
 *   try { ... } catch (err) {
 *     const fe = toFriendlyError(err, { t });
 *     notify({ tone: fe.tone, title: fe.title, message: fe.message });
 *   }
 */
export function toFriendlyError(
  err: unknown,
  translators: ErrorTranslators,
): FriendlyError {
  const { t } = translators;

  // Already-wrapped Error instance from our own code paths.
  if (err instanceof Error && !axios.isAxiosError(err)) {
    return {
      title: t('unexpected.title'),
      message: err.message || t('unexpected.message'),
      tone: 'error',
    };
  }

  if (!axios.isAxiosError(err)) {
    return {
      title: t('unexpected.title'),
      message: t('unexpected.message'),
      tone: 'error',
    };
  }

  // Network / CORS / backend down — no `response` object at all.
  if (!err.response) {
    return {
      title: t('network.title'),
      message: t('network.message'),
      tone: 'error',
    };
  }

  const status = err.response.status;
  const detail = (err.response.data as { detail?: unknown })?.detail;

  // Structured violations (422 / 429)
  if (
    detail &&
    typeof detail === 'object' &&
    Array.isArray((detail as { violations?: unknown }).violations)
  ) {
    const violations = (detail as { violations: Violation[] }).violations;
    const messages = violations.map((v) => renderViolation(v, translators));
    const tone: ErrorTone = status === 429 ? 'warning' : 'error';
    return {
      title:
        status === 429 ? t('rateLimited.title') : t('generic.title'),
      message:
        messages.length === 1
          ? messages[0]
          : messages.map((m) => `• ${m}`).join('\n'),
      tone,
      retryAfter: extractRetryAfter(err),
    };
  }

  // Plain `detail: "string"`
  if (typeof detail === 'string') {
    return {
      title: statusTitle(status, t),
      message: detail,
      tone: status >= 500 ? 'error' : 'warning',
    };
  }

  return {
    title: statusTitle(status, t),
    message: `HTTP ${status}`,
    tone: status >= 500 ? 'error' : 'warning',
  };
}
