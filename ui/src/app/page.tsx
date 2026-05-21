'use client';

import React, { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useTranslations, useLocale as useNextIntlLocale } from 'next-intl';
import { getClaims, submitClaim, Claim, ClaimStatus } from '@/lib/api';
import { AI_BRAND } from '@/lib/ai';
import { toFriendlyError } from '@/lib/errors';
import { useSnackbar } from '@/lib/snackbar';
import { LanguageToggle } from '@/lib/i18n';
import { useClaimStream } from '@/lib/use-claim-stream';
import { LiveStreamTimeline } from '@/components/LiveStreamTimeline';
import { RefreshCw, Plus, Upload, X, Sparkles } from 'lucide-react';

type TabValue = ClaimStatus | 'ALL';

const TABS: { key: string; value: TabValue }[] = [
  { key: 'all',            value: 'ALL' },
  { key: 'manualReview',   value: 'MANUAL_REVIEW' },
  { key: 'awaitingInput',  value: 'AWAITING_INPUT' },
  { key: 'approved',       value: 'APPROVED' },
  { key: 'rejected',       value: 'REJECTED' },
  { key: 'pending',        value: 'PENDING' },
];

const STATUS_STYLES: Record<string, string> = {
  APPROVED: 'bg-green-900/40 text-green-300 border border-green-700',
  REJECTED: 'bg-red-900/40 text-red-300 border border-red-700',
  MANUAL_REVIEW: 'bg-amber-900/40 text-amber-300 border border-amber-700',
  AWAITING_INPUT: 'bg-blue-900/40 text-blue-300 border border-blue-700',
  PENDING: 'bg-slate-700/60 text-slate-300 border border-slate-600',
};

const SEVERITY_STYLES: Record<string, string> = {
  LOW: 'text-emerald-400',
  HIGH: 'text-orange-400',
  TOTAL_LOSS: 'text-red-400',
};

// ── Submission Modal ─────────────────────────────────────────
function SubmitModal({
  onClose,
  onSuccess,
}: {
  onClose: () => void;
  onSuccess: (persistedClaimId: number | null) => void;
}) {
  const t = useTranslations('submit');
  const tErr = useTranslations('errors');
  const { notify } = useSnackbar();
  const fileRef = useRef<HTMLInputElement>(null);
  const [pdf, setPdf] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  // E2.2 — generate a request id at submit time so we can subscribe to
  // the SSE stream in parallel with the POST. Null when idle.
  const [requestId, setRequestId] = useState<string | null>(null);
  const stream = useClaimStream(requestId);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pdf) {
      notify.warning(t('noFile'));
      return;
    }
    setLoading(true);
    // `crypto.randomUUID` is available in every modern browser + the
    // Next.js dev server. Fallback to a timestamp+random for older
    // build targets.
    const rid =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    setRequestId(rid);
    try {
      const res = await submitClaim(pdf, rid);
      if (res.pipeline_status !== 'SUCCESS') {
        notify.error({
          title: t('pipelineErrorTitle', { status: res.pipeline_status }),
          message: res.error_message || t('pipelineErrorMessage', { brand: AI_BRAND.name }),
        });
        setLoading(false);
        return;
      }
      notify.success({
        title: t('successTitle'),
        message: t('successMessage', { brand: AI_BRAND.name }),
      });
      onSuccess(res.persisted_claim_id);
    } catch (err) {
      const fe = toFriendlyError(err, { t: tErr });
      notify({ tone: fe.tone, title: fe.title, message: fe.message });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      <div className="relative glass w-full max-w-lg p-6 space-y-5">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-bold gradient-text">{t('title')}</h2>
          <button onClick={onClose} className="text-white/40 hover:text-white/80 transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        <p className="text-white/50 text-xs leading-relaxed">
          {t('description', { brand: AI_BRAND.name })}
        </p>

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
              {pdf ? pdf.name : t('uploadHint')}
            </p>
            {pdf && (
              <p className="text-xs text-white/40 mt-1">
                {t('fileSize', { kb: (pdf.size / 1024).toFixed(1) })}
              </p>
            )}
          </div>

          {requestId && (
            <LiveStreamTimeline
              events={stream.events}
              status={stream.status}
            />
          )}

          <div className="flex gap-3 pt-1">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="flex-1 py-2 text-sm text-white/50 border border-white/10 rounded-lg hover:text-white/80 transition-colors disabled:opacity-50"
            >
              <CommonCancel />
            </button>
            <button
              type="submit"
              disabled={loading || !pdf}
              className="flex-1 py-2 text-sm font-semibold bg-purple-600/50 hover:bg-purple-600/70 border border-purple-500 text-purple-100 rounded-lg transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {loading && <RefreshCw className="w-3 h-3 animate-spin" />}
              {loading ? t('submitting') : t('submit')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// Tiny helpers to avoid importing useTranslations in every leaf
function CommonCancel() {
  const t = useTranslations('common');
  return <>{t('cancel')}</>;
}

// ── Dashboard ────────────────────────────────────────────────
export default function Dashboard() {
  const t = useTranslations('dashboard');
  const tCommon = useTranslations('common');
  const tStatus = useTranslations('status');
  const tSev = useTranslations('severity');
  const tAi = useTranslations('ai');
  const tErr = useTranslations('errors');
  const locale = useNextIntlLocale();
  const { notify } = useSnackbar();
  const [claims, setClaims] = useState<Claim[]>([]);
  const [activeTab, setActiveTab] = useState<TabValue>('ALL');
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);

  const fetchClaims = async () => {
    setLoading(true);
    try {
      const data = await getClaims(activeTab === 'ALL' ? undefined : activeTab);
      setClaims(data);
    } catch (err) {
      const fe = toFriendlyError(err, { t: tErr });
      notify({ tone: fe.tone, title: fe.title, message: fe.message });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchClaims();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  const formatDate = (d: string | null) =>
    d
      ? new Date(d).toLocaleDateString(locale === 'es' ? 'es-MX' : 'en-US', {
          month: 'short',
          day: 'numeric',
          year: 'numeric',
        })
      : tCommon('dash');

  return (
    <div className="w-full max-w-6xl mx-auto space-y-6">
      {showModal && (
        <SubmitModal
          onClose={() => setShowModal(false)}
          onSuccess={() => {
            setShowModal(false);
            fetchClaims();
          }}
        />
      )}

      {/* Header */}
      <div className="flex items-center justify-between pt-4 flex-wrap gap-3">
        <div>
          <div className="flex items-center gap-2 text-white/40 text-xs mb-1">
            <Sparkles className="w-3.5 h-3.5 text-purple-300" />
            <span>{tAi('assistedBy', { brand: tAi('fullName') })}</span>
          </div>
          <h1 className="text-3xl font-bold gradient-text">{t('title')}</h1>
          <p className="text-white/50 text-sm mt-1">
            {t('subtitle', { brand: AI_BRAND.name })}
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <LanguageToggle />
          <button
            onClick={fetchClaims}
            className="flex items-center gap-2 px-4 py-2 glass-sm hover:bg-white/10 transition-colors rounded-lg text-white/70 text-sm"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            {tCommon('refresh')}
          </button>
          <button
            onClick={() => setShowModal(true)}
            className="flex items-center gap-2 px-4 py-2 bg-purple-600/50 hover:bg-purple-600/70 border border-purple-500 text-purple-100 rounded-lg text-sm font-semibold transition-colors"
          >
            <Plus className="w-4 h-4" />
            {t('newClaim')}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 flex-wrap">
        {TABS.map((tab) => (
          <button
            key={tab.value}
            onClick={() => setActiveTab(tab.value)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              activeTab === tab.value
                ? 'bg-purple-600/60 text-white border border-purple-500'
                : 'glass-sm text-white/60 hover:text-white hover:bg-white/10'
            }`}
          >
            {t(`tabs.${tab.key}`)}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="glass overflow-hidden p-0">
        {loading ? (
          <div className="p-12 text-center text-white/40">
            <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-3" />
            <p>{t('loading')}</p>
          </div>
        ) : claims.length === 0 ? (
          <div className="p-12 text-center text-white/40">
            <p>{t('empty')}</p>
            <button
              onClick={() => setShowModal(true)}
              className="mt-4 text-purple-400 hover:text-purple-300 text-sm underline"
            >
              {t('submitFirst')}
            </button>
          </div>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-white/10 text-left text-white/50 text-xs uppercase tracking-wider">
                <th className="px-6 py-4">{t('table.claimId')}</th>
                <th className="px-6 py-4">{t('table.user')}</th>
                <th className="px-6 py-4">{t('table.policy')}</th>
                <th className="px-6 py-4">{t('table.incidentDate')}</th>
                <th className="px-6 py-4">{t('table.severity')}</th>
                <th className="px-6 py-4">{t('table.status')}</th>
                <th className="px-6 py-4">{t('table.submitted')}</th>
                <th className="px-6 py-4">{t('table.action')}</th>
              </tr>
            </thead>
            <tbody>
              {claims.map((claim, i) => (
                <tr
                  key={claim.claim_id}
                  className={`border-b border-white/5 hover:bg-white/5 transition-colors ${
                    i % 2 === 0 ? '' : 'bg-white/[0.02]'
                  }`}
                >
                  <td className="px-6 py-4">
                    <span className="font-mono text-xs text-white/70">
                      #{claim.claim_id}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-white/80 text-sm">
                    {claim.user_id}
                  </td>
                  <td className="px-6 py-4 text-white/80 text-sm">
                    {claim.policy_id}
                  </td>
                  <td className="px-6 py-4 text-white/70 text-sm">
                    {formatDate(claim.incident_date)}
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={`text-sm font-semibold ${
                        SEVERITY_STYLES[claim.severity_label] ?? 'text-white/60'
                      }`}
                    >
                      {claim.severity_label
                        ? tSev(claim.severity_label as 'LOW' | 'HIGH' | 'TOTAL_LOSS')
                        : tCommon('dash')}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={`text-xs px-2 py-1 rounded-full font-medium ${
                        STATUS_STYLES[claim.claim_status] ?? ''
                      }`}
                    >
                      {claim.claim_status
                        ? tStatus(claim.claim_status as 'APPROVED' | 'REJECTED' | 'MANUAL_REVIEW' | 'AWAITING_INPUT' | 'PENDING')
                        : tCommon('dash')}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-white/50 text-sm">
                    {formatDate(claim.created_at)}
                  </td>
                  <td className="px-6 py-4">
                    <Link
                      href={`/claims/${claim.claim_id}`}
                      className="px-3 py-1.5 text-xs font-semibold bg-purple-600/40 hover:bg-purple-600/70 border border-purple-500/50 text-purple-200 rounded-lg transition-colors"
                    >
                      {t('table.review')}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {!loading && claims.length > 0 && (
        <p className="text-white/30 text-xs text-right">
          {t('footer', { count: claims.length })}
        </p>
      )}
    </div>
  );
}
