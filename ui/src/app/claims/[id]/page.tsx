'use client';

import React, { useEffect, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useTranslations, useLocale } from 'next-intl';
import {
  getClaimDetail,
  approveClaim,
  rejectClaim,
  reanalyzeClaim,
  reanalyzeClaimWithUpload,
  isPdfMissingError,
  getUser,
  getUserPolicies,
  Claim,
  User,
  Policy,
} from '@/lib/api';
import { parseReasoning, ReasoningBlock } from '@/lib/reasoning';
import { AI_BRAND } from '@/lib/ai';
import { toFriendlyError } from '@/lib/errors';
import { useSnackbar } from '@/lib/snackbar';
import {
  ArrowLeft,
  CheckCircle,
  XCircle,
  AlertTriangle,
  RefreshCw,
  Sparkles,
  Mail,
  ShieldCheck,
  ExternalLink,
  Upload,
  X as XIcon,
} from 'lucide-react';

const REASON_MIN_LENGTH = 20;
const REASON_MAX_LENGTH = 2000;

// ── Style maps (locale-agnostic) ─────────────────────────────
const STATUS_STYLES: Record<string, string> = {
  APPROVED: 'bg-green-900/40 text-green-300 border border-green-700',
  REJECTED: 'bg-red-900/40 text-red-300 border border-red-700',
  MANUAL_REVIEW: 'bg-amber-900/40 text-amber-300 border border-amber-700',
  AWAITING_INPUT: 'bg-blue-900/40 text-blue-300 border border-blue-700',
  PENDING: 'bg-slate-700/60 text-slate-300 border border-slate-600',
};

const SEVERITY_STYLES: Record<string, string> = {
  LOW: 'bg-emerald-900/40 text-emerald-300 border border-emerald-700',
  HIGH: 'bg-orange-900/40 text-orange-300 border border-orange-700',
  TOTAL_LOSS: 'bg-red-900/40 text-red-300 border border-red-700',
};

const DECISION_PILL_STYLES: Record<string, string> = {
  APPROVED: 'bg-green-900/40 text-green-300 border border-green-700',
  REJECTED: 'bg-red-900/40 text-red-300 border border-red-700',
  MANUAL_REVIEW: 'bg-amber-900/40 text-amber-300 border border-amber-700',
  AWAITING_INPUT: 'bg-blue-900/40 text-blue-300 border border-blue-700',
  PENDING: 'bg-slate-700/60 text-slate-300 border border-slate-600',
  UPDATED: 'bg-slate-700/60 text-slate-300 border border-slate-600',
};

const POLICY_STATUS_STYLES: Record<string, string> = {
  ACTIVE: 'bg-green-900/40 text-green-300 border border-green-700',
  EXPIRED: 'bg-red-900/40 text-red-300 border border-red-700',
  CANCELLED: 'bg-slate-700/60 text-slate-300 border border-slate-600',
  SUSPENDED: 'bg-amber-900/40 text-amber-300 border border-amber-700',
};

// ── Locale-aware helpers ─────────────────────────────────────
function useFormatters() {
  const locale = useLocale();
  const tag = locale === 'es' ? 'es-MX' : 'en-US';

  // Date-only ISO strings ('YYYY-MM-DD') get parsed by `new Date()` as
  // midnight UTC. Rendering them in a non-UTC zone shifts the day (e.g.
  // '2026-01-01' → '31 dic 2025' in Mexico). Pin to UTC so the day the
  // backend stored is the day the adjuster sees.
  const isDateOnly = (s: string) => /^\d{4}-\d{2}-\d{2}$/.test(s);

  return {
    longDate: (iso: string | null) => {
      if (!iso) return '—';
      const opts: Intl.DateTimeFormatOptions = {
        month: 'long',
        day: 'numeric',
        year: 'numeric',
      };
      if (isDateOnly(iso)) opts.timeZone = 'UTC';
      return new Date(iso).toLocaleDateString(tag, opts);
    },
    shortDate: (iso: string | null) => {
      if (!iso) return '—';
      const opts: Intl.DateTimeFormatOptions = {
        day: '2-digit',
        month: 'short',
        year: 'numeric',
      };
      if (isDateOnly(iso)) opts.timeZone = 'UTC';
      return new Date(iso).toLocaleDateString(tag, opts);
    },
    dateTime: (iso: string) => {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return iso;
      return d.toLocaleString(tag, {
        day: '2-digit',
        month: 'long',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    },
    usd: (raw: string) => {
      const n = Number(raw);
      if (Number.isNaN(n)) return raw;
      return n.toLocaleString('en-US', {
        style: 'currency',
        currency: 'USD',
        maximumFractionDigits: 2,
      });
    },
  };
}

// ── Sub-components ───────────────────────────────────────────
function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 py-3 border-b border-white/5 last:border-0">
      <span className="text-xs text-white/40 uppercase tracking-wider">{label}</span>
      <span className="text-white/90 text-sm">{value ?? '—'}</span>
    </div>
  );
}

function AiAvatar() {
  return (
    <div className="w-9 h-9 rounded-full bg-gradient-to-br from-purple-500/70 to-blue-500/70 border border-purple-400/40 flex items-center justify-center text-white font-bold text-sm flex-shrink-0">
      {AI_BRAND.avatarInitial}
    </div>
  );
}

function HumanAvatar({ name }: { name: string }) {
  const initial = name.trim().charAt(0).toUpperCase() || '?';
  return (
    <div className="w-9 h-9 rounded-full bg-white/10 border border-white/20 flex items-center justify-center text-white/80 font-bold text-sm flex-shrink-0">
      {initial}
    </div>
  );
}

function DecisionPill({ decision }: { decision: string }) {
  const t = useTranslations('decisions.past');
  const label = t(decision as 'APPROVED') || decision;
  return (
    <span
      className={`inline-flex items-center text-[11px] px-2 py-0.5 rounded-full font-semibold uppercase tracking-wider ${
        DECISION_PILL_STYLES[decision] ??
        'bg-white/10 text-white/70 border border-white/20'
      }`}
    >
      {label}
    </span>
  );
}

// ── Reasoning bubbles ────────────────────────────────────────
function RecommendationBubble({
  decision,
  notes,
}: {
  decision: string;
  notes: string;
}) {
  const t = useTranslations('claim.reasoning');
  const tVerb = useTranslations('decisions.verb');
  const verb = tVerb(decision as 'APPROVED') || decision.toLowerCase();
  return (
    <div className="flex gap-3">
      <AiAvatar />
      <div className="flex-1 glass-sm rounded-lg p-4 space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-white/90 text-sm font-semibold">{AI_BRAND.name}</span>
          <Sparkles className="w-3.5 h-3.5 text-purple-300" />
          <span className="text-white/40 text-xs">{t('recommends')}</span>
          <DecisionPill decision={decision} />
        </div>
        <p className="text-white/40 text-xs">{t('summary', { verb })}</p>
        {notes && (
          <div className="bg-black/20 border border-white/5 rounded-lg p-3 mt-2">
            <p className="text-[11px] text-white/40 uppercase tracking-wider mb-1">
              {t('analystNotes')}
            </p>
            <p className="text-white/80 text-sm leading-relaxed whitespace-pre-wrap">
              {notes}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function OverrideBubble({
  decision,
  actor,
  timestamp,
  reason,
}: {
  decision: string;
  actor: string;
  timestamp: string;
  reason: string;
}) {
  const t = useTranslations('claim.reasoning');
  const { dateTime } = useFormatters();
  return (
    <div className="flex gap-3">
      <HumanAvatar name={actor} />
      <div className="flex-1 glass-sm rounded-lg p-4 space-y-2 border-l-2 border-purple-500/30">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-white/90 text-sm font-semibold">{actor}</span>
          <span className="text-white/40 text-xs">{t('markedAs')}</span>
          <DecisionPill decision={decision} />
          <span className="text-white/30 text-xs ml-auto">{dateTime(timestamp)}</span>
        </div>
        {reason && (
          <div className="bg-black/20 border border-white/5 rounded-lg p-3">
            <p className="text-[11px] text-white/40 uppercase tracking-wider mb-1">
              {t('adjusterReason')}
            </p>
            <p className="text-white/80 text-sm leading-relaxed whitespace-pre-wrap italic">
              “{reason}”
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function ReanalysisBubble({
  decision,
  actor,
  timestamp,
  notes,
}: {
  decision: string;
  actor: string;
  timestamp: string;
  notes: string;
}) {
  const t = useTranslations('claim.reasoning');
  const tVerb = useTranslations('decisions.verb');
  const { dateTime } = useFormatters();
  const verb = tVerb(decision as 'APPROVED') || decision.toLowerCase();
  return (
    <div className="flex gap-3">
      <AiAvatar />
      <div className="flex-1 glass-sm rounded-lg p-4 space-y-2 border-l-2 border-blue-500/40">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-white/90 text-sm font-semibold">{AI_BRAND.name}</span>
          <RefreshCw className="w-3.5 h-3.5 text-blue-300" />
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-900/40 text-blue-300 border border-blue-700 uppercase tracking-wider font-semibold">
            {t('reanalysisTag')}
          </span>
          <DecisionPill decision={decision} />
          <span className="text-white/30 text-xs ml-auto">{dateTime(timestamp)}</span>
        </div>
        <p className="text-white/40 text-xs">{t('reanalysisSummary', { verb })}</p>
        {notes && (
          <div className="bg-black/20 border border-white/5 rounded-lg p-3 mt-2">
            <p className="text-[11px] text-white/40 uppercase tracking-wider mb-1">
              {t('analystNotes')}
            </p>
            <p className="text-white/80 text-sm leading-relaxed whitespace-pre-wrap">
              {notes}
            </p>
          </div>
        )}
        <p className="text-[10px] text-white/30 italic">
          {actor}
        </p>
      </div>
    </div>
  );
}

function NoteBubble({ text }: { text: string }) {
  const tAi = useTranslations('ai');
  return (
    <div className="flex gap-3">
      <AiAvatar />
      <div className="flex-1 glass-sm rounded-lg p-4">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-white/90 text-sm font-semibold">{AI_BRAND.name}</span>
          <span className="text-white/40 text-xs">{tAi('tagline')}</span>
        </div>
        <p className="text-white/80 text-sm leading-relaxed whitespace-pre-wrap">{text}</p>
      </div>
    </div>
  );
}

function ReasoningPanel({ claim }: { claim: Claim }) {
  const t = useTranslations('claim.reasoning');
  const blocks: ReasoningBlock[] = parseReasoning(claim.ai_reasoning);

  if (blocks.length === 0) {
    return (
      <div className="glass-sm rounded-lg p-4 text-white/40 text-sm flex items-center gap-3">
        <AiAvatar />
        <span>{t('empty', { brand: AI_BRAND.name })}</span>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {blocks.map((b, i) => {
        if (b.kind === 'recommendation') {
          return <RecommendationBubble key={i} decision={b.decision} notes={b.notes} />;
        }
        if (b.kind === 'override') {
          return (
            <OverrideBubble
              key={i}
              decision={b.decision}
              actor={b.actor}
              timestamp={b.timestamp}
              reason={b.reason}
            />
          );
        }
        if (b.kind === 'reanalysis') {
          return (
            <ReanalysisBubble
              key={i}
              decision={b.decision}
              actor={b.actor}
              timestamp={b.timestamp}
              notes={b.notes}
            />
          );
        }
        return <NoteBubble key={i} text={b.text} />;
      })}
    </div>
  );
}

// ── Action panel ─────────────────────────────────────────────
function ActionPanel({
  claim,
  onDone,
}: {
  claim: Claim;
  onDone: (updated: Claim) => void;
}) {
  const t = useTranslations('claim.action');
  const tStatus = useTranslations('status');
  const tErr = useTranslations('errors');
  const { notify } = useSnackbar();
  const [mode, setMode] = useState<'idle' | 'approve' | 'reject'>('idle');
  const [reason, setReason] = useState('');
  const [loading, setLoading] = useState(false);

  const isResolved =
    claim.claim_status === 'APPROVED' || claim.claim_status === 'REJECTED';
  const isActionable =
    claim.claim_status === 'MANUAL_REVIEW' ||
    claim.claim_status === 'AWAITING_INPUT';

  const trimmedLen = reason.trim().length;
  const reasonValid =
    trimmedLen >= REASON_MIN_LENGTH && reason.length <= REASON_MAX_LENGTH;
  const charsRemaining = REASON_MIN_LENGTH - trimmedLen;

  const localizedStatus = (s: string) => {
    const key = s as 'APPROVED' | 'REJECTED' | 'MANUAL_REVIEW' | 'AWAITING_INPUT' | 'PENDING';
    return tStatus(key);
  };

  const submit = async (action: 'approve' | 'reject') => {
    if (!reasonValid) {
      notify.warning({
        title: t('reasonTooShortTitle'),
        message: t('reasonTooShortMessage', { min: REASON_MIN_LENGTH }),
      });
      return;
    }
    setLoading(true);
    try {
      const updated =
        action === 'approve'
          ? await approveClaim(claim.claim_id, reason)
          : await rejectClaim(claim.claim_id, reason);
      onDone(updated);
      notify.success({
        title:
          action === 'approve'
            ? t('successApproveTitle')
            : t('successRejectTitle'),
        message: t('successMessage', { id: claim.claim_id }),
      });
      setMode('idle');
      setReason('');
    } catch (err) {
      const fe = toFriendlyError(err, { t: tErr });
      notify({ tone: fe.tone, title: fe.title, message: fe.message });
    } finally {
      setLoading(false);
    }
  };

  if (isResolved) {
    return (
      <div className="glass p-6 text-center space-y-2">
        <div
          className={`inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-semibold ${
            STATUS_STYLES[claim.claim_status]
          }`}
        >
          {claim.claim_status === 'APPROVED' ? (
            <CheckCircle className="w-4 h-4" />
          ) : (
            <XCircle className="w-4 h-4" />
          )}
          {t('resolved', { status: localizedStatus(claim.claim_status) })}
        </div>
        <p className="text-white/40 text-xs">{t('resolvedHint')}</p>
      </div>
    );
  }

  if (!isActionable) {
    return (
      <div className="glass p-6 text-center space-y-2">
        <p className="text-white/60 text-sm">
          {t('notActionable', { status: localizedStatus(claim.claim_status) })}
        </p>
        <p className="text-white/40 text-xs">{t('notActionableHint')}</p>
      </div>
    );
  }

  return (
    <div className="glass p-6 space-y-4">
      <h3 className="text-white/90 font-semibold text-sm uppercase tracking-wider">
        {t('title')}
      </h3>

      {mode === 'idle' && (
        <div className="space-y-3">
          <div className="bg-white/5 rounded-lg p-3 text-sm text-white/60">
            {t('currentState')}{' '}
            <span
              className={`font-semibold px-2 py-0.5 rounded-full text-xs ${
                STATUS_STYLES[claim.claim_status] ?? ''
              }`}
            >
              {localizedStatus(claim.claim_status)}
            </span>
          </div>

          <button
            onClick={() => setMode('approve')}
            className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-green-700/40 hover:bg-green-700/60 border border-green-600 text-green-200 rounded-lg font-semibold text-sm transition-colors"
          >
            <CheckCircle className="w-4 h-4" />
            {t('approveButton')}
          </button>

          <button
            onClick={() => setMode('reject')}
            className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-red-700/30 hover:bg-red-700/50 border border-red-600 text-red-200 rounded-lg font-semibold text-sm transition-colors"
          >
            <XCircle className="w-4 h-4" />
            {t('rejectButton')}
          </button>
        </div>
      )}

      {mode !== 'idle' && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            {mode === 'approve' ? (
              <span className="inline-flex items-center gap-1.5 text-xs px-2 py-1 bg-green-900/40 text-green-300 border border-green-700 rounded-full">
                <CheckCircle className="w-3 h-3" />
                {t('approvingTag')}
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 text-xs px-2 py-1 bg-red-900/40 text-red-300 border border-red-700 rounded-full">
                <XCircle className="w-3 h-3" />
                {t('rejectingTag')}
              </span>
            )}
          </div>

          <div>
            <label className="text-xs text-white/50 uppercase tracking-wider block mb-2">
              {t('reasonLabel')} <span className="text-red-400">*</span>
            </label>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={4}
              maxLength={REASON_MAX_LENGTH}
              placeholder={
                mode === 'approve'
                  ? t('reasonPlaceholderApprove')
                  : t('reasonPlaceholderReject')
              }
              className={`w-full bg-white/5 border rounded-lg px-3 py-2 text-white/80 text-sm placeholder-white/30 focus:outline-none resize-none transition-colors ${
                trimmedLen === 0
                  ? 'border-white/10 focus:border-purple-500/50'
                  : reasonValid
                    ? 'border-green-600/30 focus:border-green-500/60'
                    : 'border-amber-600/30 focus:border-amber-500/60'
              }`}
              autoFocus
            />
            <div className="flex items-center justify-between mt-1">
              <p
                className={`text-xs ${
                  reasonValid ? 'text-green-400/70' : 'text-amber-400/70'
                }`}
              >
                {reasonValid
                  ? t('auditMinOk')
                  : t('auditMinShort', {
                      missing: charsRemaining,
                      min: REASON_MIN_LENGTH,
                    })}
              </p>
              <p className="text-xs text-white/30">
                {reason.length} / {REASON_MAX_LENGTH}
              </p>
            </div>
          </div>

          <div className="flex gap-2">
            <button
              onClick={() => {
                setMode('idle');
                setReason('');
              }}
              disabled={loading}
              className="flex-1 py-2 text-sm text-white/50 hover:text-white/80 border border-white/10 rounded-lg transition-colors disabled:opacity-50"
            >
              <CommonCancel />
            </button>
            <button
              onClick={() => submit(mode)}
              disabled={loading || !reasonValid}
              className={`flex-1 py-2 text-sm font-semibold rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 border ${
                mode === 'approve'
                  ? 'bg-green-700/50 hover:bg-green-700/70 border-green-500 text-green-100'
                  : 'bg-red-700/50 hover:bg-red-700/70 border-red-500 text-red-100'
              }`}
            >
              {loading && <RefreshCw className="w-3 h-3 animate-spin" />}
              {mode === 'approve' ? t('confirmApprove') : t('confirmReject')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function CommonCancel() {
  const t = useTranslations('common');
  return <>{t('cancel')}</>;
}

// ── Customer (user + policy) panel ───────────────────────────
function isIncidentAfterPolicyEnd(claim: Claim, policy: Policy): boolean {
  if (!claim.incident_date || !policy.end_date) return false;
  return claim.incident_date > policy.end_date;
}

function isIncidentBeforePolicyStart(claim: Claim, policy: Policy): boolean {
  if (!claim.incident_date || !policy.start_date) return false;
  return claim.incident_date < policy.start_date;
}

function CustomerPanel({ claim }: { claim: Claim }) {
  const t = useTranslations('claim.customer');
  const tPolStatus = useTranslations('policyStatus');
  const tErr = useTranslations('errors');
  const { notify } = useSnackbar();
  const { shortDate, usd } = useFormatters();
  const [user, setUser] = useState<User | null>(null);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([getUser(claim.user_id), getUserPolicies(claim.user_id)])
      .then(([u, ps]) => {
        if (cancelled) return;
        setUser(u);
        setPolicies(ps);
      })
      .catch((err) => {
        if (cancelled) return;
        const fe = toFriendlyError(err, { t: tErr });
        notify({ tone: fe.tone, title: fe.title, message: fe.message });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claim.user_id, claim.policy_id]);

  if (loading) {
    return (
      <div className="glass p-6 text-center text-white/40">
        <RefreshCw className="w-5 h-5 animate-spin mx-auto mb-2" />
        <p className="text-xs">{t('loading')}</p>
      </div>
    );
  }

  const linkedPolicy = policies.find((p) => p.policy_id === claim.policy_id);

  const localizedPolicyStatus = (s: string) => {
    const key = s as 'ACTIVE' | 'EXPIRED' | 'CANCELLED' | 'SUSPENDED';
    return tPolStatus(key) || s;
  };

  return (
    <div className="space-y-4">
      {/* User card */}
      {user && (
        <div className="glass p-5 space-y-3">
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="text-xs text-white/40 uppercase tracking-wider">
                {t('customer')}
              </p>
              <h3 className="text-white/90 font-semibold text-lg leading-tight mt-0.5">
                {user.name}
              </h3>
            </div>
            <span className="font-mono text-[10px] text-white/30 mt-1">
              #U-{user.user_id}
            </span>
          </div>
          <a
            href={`mailto:${user.email}`}
            className="flex items-center gap-2 text-sm text-purple-300 hover:text-purple-200 transition-colors"
          >
            <Mail className="w-3.5 h-3.5" />
            {user.email}
            <ExternalLink className="w-3 h-3 opacity-50" />
          </a>
        </div>
      )}

      {/* Linked policy card */}
      {linkedPolicy ? (
        <div className="glass p-5 space-y-3">
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-purple-300" />
              <div>
                <p className="text-xs text-white/40 uppercase tracking-wider">
                  {t('linkedPolicy')}
                </p>
                <h3 className="text-white/90 font-semibold leading-tight mt-0.5 capitalize">
                  {linkedPolicy.type}
                </h3>
              </div>
            </div>
            <span className="font-mono text-[10px] text-white/30 mt-1">
              #P-{linkedPolicy.policy_id}
            </span>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={`inline-flex text-[11px] px-2 py-0.5 rounded-full font-semibold uppercase tracking-wider ${
                POLICY_STATUS_STYLES[linkedPolicy.status] ??
                'bg-white/10 text-white/70 border border-white/20'
              }`}
            >
              {localizedPolicyStatus(linkedPolicy.status)}
            </span>
            {linkedPolicy.stored_status &&
              linkedPolicy.stored_status !== linkedPolicy.status && (
                <span
                  title={t('statusDrift', {
                    effective: localizedPolicyStatus(linkedPolicy.status),
                    stored: localizedPolicyStatus(linkedPolicy.stored_status),
                    endDate: shortDate(linkedPolicy.end_date),
                  })}
                  className="text-[10px] text-amber-300/80 italic cursor-help underline decoration-dotted"
                >
                  {t('statusDriftShort', {
                    endDate: shortDate(linkedPolicy.end_date),
                  })}
                </span>
              )}
          </div>

          <div className="grid grid-cols-2 gap-3 pt-2 border-t border-white/5">
            <div>
              <p className="text-[10px] text-white/40 uppercase tracking-wider">
                {t('validFrom')}
              </p>
              <p
                className={`text-sm font-medium mt-0.5 ${
                  isIncidentBeforePolicyStart(claim, linkedPolicy)
                    ? 'text-red-300'
                    : 'text-white/85'
                }`}
              >
                {shortDate(linkedPolicy.start_date)}
              </p>
            </div>
            <div>
              <p className="text-[10px] text-white/40 uppercase tracking-wider">
                {t('validTo')}
              </p>
              <p
                className={`text-sm font-medium mt-0.5 ${
                  isIncidentAfterPolicyEnd(claim, linkedPolicy)
                    ? 'text-red-300'
                    : 'text-white/85'
                }`}
              >
                {shortDate(linkedPolicy.end_date)}
              </p>
            </div>
            <div>
              <p className="text-[10px] text-white/40 uppercase tracking-wider">
                {t('coverageLimit')}
              </p>
              <p className="text-sm font-medium text-white/85 mt-0.5">
                {usd(linkedPolicy.coverage_limit_usd)}
              </p>
            </div>
            <div>
              <p className="text-[10px] text-white/40 uppercase tracking-wider">
                {t('deductible')}
              </p>
              <p className="text-sm font-medium text-white/85 mt-0.5">
                {usd(linkedPolicy.deductible_usd)}
              </p>
            </div>
          </div>

          {(isIncidentAfterPolicyEnd(claim, linkedPolicy) ||
            isIncidentBeforePolicyStart(claim, linkedPolicy)) && (
            <div className="flex items-start gap-2 text-amber-300 text-xs bg-amber-900/30 border border-amber-800 rounded-lg px-3 py-2 mt-1">
              <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
              <span>
                {t('outOfCoverage', { date: shortDate(claim.incident_date) })}
              </span>
            </div>
          )}
        </div>
      ) : (
        <div className="glass p-5 text-center space-y-1">
          <AlertTriangle className="w-5 h-5 text-amber-400 mx-auto" />
          <p className="text-sm text-white/80">
            {t('policyMissing', { id: claim.policy_id })}
          </p>
          <p className="text-xs text-white/40">{t('policyMissingHint')}</p>
        </div>
      )}

      {/* Other policies */}
      {policies.length > 1 && (
        <details className="glass-sm rounded-lg p-3 group">
          <summary className="cursor-pointer text-xs text-white/50 hover:text-white/80 transition-colors flex items-center justify-between">
            <span>{t('otherPolicies', { count: policies.length - 1 })}</span>
            <span className="text-white/30 group-open:rotate-180 transition-transform">▾</span>
          </summary>
          <div className="mt-3 space-y-2">
            {policies
              .filter((p) => p.policy_id !== claim.policy_id)
              .map((p) => (
                <div
                  key={p.policy_id}
                  className="flex items-center justify-between text-xs bg-white/[0.03] rounded px-2 py-1.5"
                >
                  <div>
                    <span className="capitalize text-white/80">{p.type}</span>
                    <span className="font-mono text-white/30 ml-2">#P-{p.policy_id}</span>
                  </div>
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                      POLICY_STATUS_STYLES[p.status] ?? 'bg-white/10 text-white/50'
                    }`}
                  >
                    {localizedPolicyStatus(p.status)}
                  </span>
                </div>
              ))}
          </div>
        </details>
      )}
    </div>
  );
}

// ── Re-analyze button (lives in reasoning panel header) ─────
//
// Tries the stored-PDF path first. If the backend returns 409
// (pdf_missing), opens a small modal asking the adjuster to re-upload
// the document. Once uploaded, the new PDF replaces the missing one
// on the volume so subsequent re-analyses don't need to re-upload.

function ReuploadModal({
  claim,
  onClose,
  onSuccess,
}: {
  claim: Claim;
  onClose: () => void;
  onSuccess: (c: Claim) => void;
}) {
  const t = useTranslations('claim.reasoning');
  const tCommon = useTranslations('common');
  const tErr = useTranslations('errors');
  const tSubmit = useTranslations('submit');
  const { notify } = useSnackbar();
  const fileRef = useRef<HTMLInputElement>(null);
  const [pdf, setPdf] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [inlineError, setInlineError] = useState<string>('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setInlineError('');
    if (!pdf) {
      notify.warning(tSubmit('noFile'));
      return;
    }
    setLoading(true);
    try {
      const updated = await reanalyzeClaimWithUpload(claim.claim_id, pdf);
      onSuccess(updated);
      notify.success({
        title: t('reanalyzeSuccessTitle'),
        message: t('reanalyzeSuccessMessage', {
          brand: AI_BRAND.name,
          id: claim.claim_id,
        }),
      });
    } catch (err) {
      const fe = toFriendlyError(err, { t: tErr });
      // Show in BOTH places: snackbar (top-right) and inline inside the
      // modal so the failure is impossible to miss while focused on the
      // upload form.
      notify({ tone: fe.tone, title: fe.title, message: fe.message });
      setInlineError(`${fe.title}: ${fe.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={loading ? undefined : onClose}
      />
      <div className="relative glass w-full max-w-lg p-6 space-y-5">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-bold gradient-text">{t('reuploadTitle')}</h2>
          <button
            onClick={onClose}
            disabled={loading}
            className="text-white/40 hover:text-white/80 transition-colors disabled:opacity-50"
            aria-label={tCommon('close')}
          >
            <XIcon className="w-5 h-5" />
          </button>
        </div>

        <p className="text-white/50 text-xs leading-relaxed">
          {t('reuploadDescription', { brand: AI_BRAND.name })}
        </p>

        {inlineError && (
          <div className="flex items-start gap-2 text-red-300 text-sm bg-red-900/30 border border-red-800 rounded-lg px-3 py-2">
            <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <span className="whitespace-pre-wrap">{inlineError}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div
            onClick={() => fileRef.current?.click()}
            className="border-2 border-dashed border-white/20 rounded-lg p-8 text-center cursor-pointer hover:border-purple-500/50 transition-colors"
          >
            <input
              ref={fileRef}
              type="file"
              accept="application/pdf,.pdf"
              className="hidden"
              onChange={(e) => setPdf(e.target.files?.[0] || null)}
            />
            <Upload className="w-7 h-7 text-purple-400 mx-auto mb-2" />
            <p className="text-sm text-white/70">
              {pdf ? pdf.name : tSubmit('uploadHint')}
            </p>
            {pdf && (
              <p className="text-xs text-white/40 mt-1">
                {tSubmit('fileSize', { kb: (pdf.size / 1024).toFixed(1) })}
              </p>
            )}
          </div>

          {loading && (
            <p className="text-xs text-white/50 text-center">
              {tSubmit('analyzing', { brand: AI_BRAND.name })}
            </p>
          )}

          <div className="flex gap-3 pt-1">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="flex-1 py-2 text-sm text-white/50 border border-white/10 rounded-lg hover:text-white/80 transition-colors disabled:opacity-50"
            >
              {tCommon('cancel')}
            </button>
            <button
              type="submit"
              disabled={loading || !pdf}
              className="flex-1 py-2 text-sm font-semibold bg-purple-600/50 hover:bg-purple-600/70 border border-purple-500 text-purple-100 rounded-lg transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {loading && <RefreshCw className="w-3 h-3 animate-spin" />}
              {t('reuploadConfirm')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function ReanalyzeButton({
  claim,
  onUpdated,
}: {
  claim: Claim;
  onUpdated: (c: Claim) => void;
}) {
  const t = useTranslations('claim.reasoning');
  const tErr = useTranslations('errors');
  const { notify } = useSnackbar();
  const [loading, setLoading] = useState(false);
  const [showUpload, setShowUpload] = useState(false);

  const run = async () => {
    setLoading(true);
    try {
      const updated = await reanalyzeClaim(claim.claim_id);
      onUpdated(updated);
      notify.success({
        title: t('reanalyzeSuccessTitle'),
        message: t('reanalyzeSuccessMessage', {
          brand: AI_BRAND.name,
          id: claim.claim_id,
        }),
      });
    } catch (err) {
      // The backend tells us "PDF missing" via 409 + structured detail.
      // Treat that as a happy-path branch: open the upload modal instead
      // of surfacing it as an error toast.
      if (isPdfMissingError(err)) {
        setShowUpload(true);
      } else {
        const fe = toFriendlyError(err, { t: tErr });
        notify({ tone: fe.tone, title: fe.title, message: fe.message });
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <button
        onClick={run}
        disabled={loading}
        className="flex items-center gap-2 px-3 py-1.5 glass-sm hover:bg-white/10 transition-colors rounded-lg text-white/70 text-xs disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
        {loading ? t('reanalyzing') : t('reanalyzeButton')}
      </button>

      {showUpload && (
        <ReuploadModal
          claim={claim}
          onClose={() => setShowUpload(false)}
          onSuccess={(updated) => {
            onUpdated(updated);
            setShowUpload(false);
          }}
        />
      )}
    </>
  );
}

// ── Main Page ────────────────────────────────────────────────
export default function ClaimDetailPage() {
  const t = useTranslations('claim');
  const tInfo = useTranslations('claim.info');
  const tReason = useTranslations('claim.reasoning');
  const tTimeline = useTranslations('claim.timeline');
  const tStatus = useTranslations('status');
  const tSev = useTranslations('severity');
  const tAi = useTranslations('ai');
  const tErr = useTranslations('errors');
  const { notify } = useSnackbar();
  const { longDate } = useFormatters();
  const params = useParams();
  const router = useRouter();
  const claimIdRaw = params.id as string;
  const claimId = parseInt(claimIdRaw, 10);

  const [claim, setClaim] = useState<Claim | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    if (Number.isNaN(claimId)) {
      setError(t('invalidId', { id: claimIdRaw }));
      setLoading(false);
      return;
    }
    setLoading(true);
    getClaimDetail(claimId)
      .then(setClaim)
      .catch((err) => {
        const fe = toFriendlyError(err, { t: tErr });
        notify({ tone: fe.tone, title: fe.title, message: fe.message });
        setError(fe.message);
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claimId, claimIdRaw]);

  if (loading) {
    return (
      <div className="w-full max-w-6xl mx-auto pt-8 text-center text-white/40">
        <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-3" />
        <p>{t('loading')}</p>
      </div>
    );
  }

  if (error || !claim) {
    return (
      <div className="w-full max-w-6xl mx-auto pt-8">
        <div className="glass p-6 border-l-4 border-red-500 text-red-200 flex items-center gap-3">
          <AlertTriangle className="w-5 h-5" />
          {error || t('notFound')}
        </div>
        <button
          onClick={() => router.push('/')}
          className="mt-4 text-white/50 hover:text-white/80 flex items-center gap-2 text-sm"
        >
          <ArrowLeft className="w-4 h-4" /> {t('backToDashboard')}
        </button>
      </div>
    );
  }

  const localizedStatus = (s: string) =>
    tStatus(s as 'APPROVED' | 'REJECTED' | 'MANUAL_REVIEW' | 'AWAITING_INPUT' | 'PENDING');
  const localizedSeverity = (s: string) =>
    tSev(s as 'LOW' | 'HIGH' | 'TOTAL_LOSS');

  return (
    <div className="w-full max-w-6xl mx-auto space-y-6 pb-12">
      {/* Back + Header */}
      <div className="pt-4">
        <button
          onClick={() => router.push('/')}
          className="flex items-center gap-2 text-white/40 hover:text-white/70 text-sm mb-4 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          {t('backToDashboard')}
        </button>
        <div className="flex flex-wrap items-start gap-4 justify-between">
          <div>
            <h1 className="text-2xl font-bold gradient-text">
              {t('title', { id: claim.claim_id })}
            </h1>
            <p className="text-white/40 text-xs mt-1">
              {t('subtitle', { userId: claim.user_id, policyId: claim.policy_id })}
            </p>
          </div>
          <div className="flex gap-2">
            <span
              className={`text-xs px-3 py-1 rounded-full font-semibold ${
                SEVERITY_STYLES[claim.severity_label] ?? ''
              }`}
            >
              {localizedSeverity(claim.severity_label)}
            </span>
            <span
              className={`text-xs px-3 py-1 rounded-full font-semibold ${
                STATUS_STYLES[claim.claim_status] ?? ''
              }`}
            >
              {localizedStatus(claim.claim_status)}
            </span>
          </div>
        </div>
      </div>

      {/* Two-column layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* LEFT — Claim Data + AI Reasoning */}
        <div className="lg:col-span-2 space-y-6">
          <div className="glass p-6 space-y-1">
            <h2 className="text-white/80 font-semibold text-sm mb-3 uppercase tracking-wider">
              {tInfo('title')}
            </h2>
            <InfoRow label={tInfo('userId')} value={claim.user_id} />
            <InfoRow label={tInfo('policyId')} value={claim.policy_id} />
            <InfoRow label={tInfo('incidentDate')} value={longDate(claim.incident_date)} />
            <InfoRow label={tInfo('incidentLocation')} value={claim.incident_location} />
            <InfoRow
              label={tInfo('reportedDamage')}
              value={
                claim.reported_damage_amount
                  ? `$${claim.reported_damage_amount}`
                  : '—'
              }
            />
            <InfoRow
              label={tInfo('description')}
              value={
                <span className="leading-relaxed">
                  {claim.incident_description || '—'}
                </span>
              }
            />
            <InfoRow label={tInfo('submitted')} value={longDate(claim.created_at)} />
          </div>

          <div className="glass p-6 space-y-4">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <h2 className="text-white/80 font-semibold text-sm uppercase tracking-wider">
                {tReason('title')}
              </h2>
              <div className="flex items-center gap-3">
                <span className="text-white/40 text-xs">{tAi('fullName')}</span>
                <ReanalyzeButton claim={claim} onUpdated={setClaim} />
              </div>
            </div>
            <ReasoningPanel claim={claim} />
          </div>
        </div>

        {/* RIGHT — Action Panel + Customer / Policy context */}
        <div className="lg:col-span-1 space-y-4">
          <ActionPanel claim={claim} onDone={setClaim} />

          <CustomerPanel claim={claim} />

          <div className="glass-sm p-4 space-y-2">
            <h3 className="text-white/40 text-xs uppercase tracking-wider">
              {tTimeline('title')}
            </h3>
            <div className="text-xs text-white/50 space-y-1">
              <div className="flex justify-between">
                <span>{tTimeline('submitted')}</span>
                <span>{longDate(claim.created_at)}</span>
              </div>
              <div className="flex justify-between">
                <span>{tTimeline('lastUpdated')}</span>
                <span>{longDate(claim.updated_at)}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
