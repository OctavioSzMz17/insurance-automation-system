'use client';

/**
 * LiveStreamTimeline — "Lía está pensando" panel (Phase E2.2).
 *
 * Renders a chronological list of pipeline events arriving via
 * useClaimStream(). Each tool.call shows up as a "doing" row that
 * later flips to "done" when its tool.result lands (matched on the
 * `name` field within the same stage). Agent.start / agent.end
 * collapse to subtle stage headers.
 *
 * Designed to make a ~30-second wait feel transparent, not anxious.
 */

import React from 'react';
import { useTranslations } from 'next-intl';
import {
  CheckCircle2,
  XCircle,
  Loader2,
  Sparkles,
  AlertTriangle,
} from 'lucide-react';

import {
  StreamEvent,
  StreamStatus,
} from '@/lib/use-claim-stream';
import { AI_BRAND } from '@/lib/ai';

// ── Helpers ──────────────────────────────────────────────────────

interface TimelineRow {
  key:         string;
  kind:        'tool' | 'agent' | 'lifecycle';
  label:       string;
  stage?:      string;
  state:       'pending' | 'done' | 'error';
  latency_ms?: number;
}

function latencyBadge(ms?: number): string {
  if (ms == null) return '';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function latencyTone(ms?: number): string {
  if (ms == null) return 'text-white/40';
  if (ms < 1000) return 'text-emerald-300';
  if (ms < 5000) return 'text-amber-300';
  return 'text-red-300';
}

/**
 * Fold the raw events into rows. tool.call + tool.result with same
 * stage+name collapse to one row that flips from pending → done.
 * agent.start / agent.end become a single stage row.
 */
function foldRows(
  events: StreamEvent[],
  t: (k: string, vals?: Record<string, string | number>) => string,
  tTool: (k: string, vals?: Record<string, string | number>) => string,
): TimelineRow[] {
  const rows: TimelineRow[] = [];

  // Index for matching call → result and start → end.
  // Key by (kind, stage, name) so two stages calling the same tool
  // don't collide.
  const indexByKey: Map<string, number> = new Map();

  const upsert = (
    key:        string,
    base:       Omit<TimelineRow, 'key'> & { key?: string },
  ) => {
    const existing = indexByKey.get(key);
    if (existing != null) {
      rows[existing] = { ...rows[existing], ...base, key };
    } else {
      const idx = rows.length;
      rows.push({ ...base, key });
      indexByKey.set(key, idx);
    }
  };

  for (const ev of events) {
    const stage = ev.stage || 'system';
    const name  = ev.name  || '';

    if (ev.type === 'pipeline.start') {
      upsert('lc:start', {
        kind:  'lifecycle',
        stage: 'system',
        label: t('pipelineStart'),
        state: 'done',
      });
      continue;
    }
    if (ev.type === 'pipeline.done') {
      upsert('lc:done', {
        kind:       'lifecycle',
        stage:      'system',
        label:      t('pipelineDone', { latency: latencyBadge(ev.latency_ms) }),
        state:      'done',
        latency_ms: ev.latency_ms,
      });
      continue;
    }
    if (ev.type === 'pipeline.error') {
      upsert('lc:error', {
        kind:  'lifecycle',
        stage: 'system',
        label: t('errorLabel'),
        state: 'error',
      });
      continue;
    }

    if (ev.type === 'agent.start') {
      const labelKey =
        stage === 'auditor' ? 'agentStartAuditor' : 'agentStartAnalyst';
      upsert(`agent:${stage}`, {
        kind:  'agent',
        stage,
        label: t(labelKey),
        state: 'pending',
      });
      continue;
    }
    if (ev.type === 'agent.end') {
      const labelKey =
        stage === 'auditor' ? 'agentEndAuditor' : 'agentEndAnalyst';
      upsert(`agent:${stage}`, {
        kind:       'agent',
        stage,
        label:      t(labelKey),
        state:      'done',
        latency_ms: ev.latency_ms,
      });
      continue;
    }

    if (ev.type === 'agent.decision') {
      const decision = (ev.data?.['decision'] as string | undefined) || '—';
      upsert('lc:decision', {
        kind:  'lifecycle',
        stage: 'system',
        label: t('agentDecision', { decision }),
        state: 'done',
      });
      continue;
    }

    if (ev.type === 'tool.call') {
      const key = `tool:${stage}:${name}`;
      const label = tTool(name, { name });
      // If next-intl missed the key, it returns the key — coerce to a
      // friendly default.
      const safeLabel =
        label === name ? tTool('default', { name }) : label;
      upsert(key, {
        kind:  'tool',
        stage,
        label: safeLabel,
        state: 'pending',
      });
      continue;
    }

    if (ev.type === 'tool.result') {
      const key = `tool:${stage}:${name}`;
      const hadError = ev.data?.['error'] != null;
      upsert(key, {
        kind:       'tool',
        stage,
        label: tTool(name) === name ? tTool('default', { name }) : tTool(name),
        state:      hadError ? 'error' : 'done',
        latency_ms: ev.latency_ms,
      });
      continue;
    }
  }
  return rows;
}

// ── Stage badge ──────────────────────────────────────────────────

const STAGE_BADGE_STYLES: Record<string, string> = {
  analyst: 'text-purple-300 border-purple-700/50 bg-purple-900/30',
  auditor: 'text-blue-300 border-blue-700/50 bg-blue-900/30',
  system:  'text-white/50 border-white/10 bg-white/[0.04]',
};

function StageDot({ stage }: { stage?: string }) {
  const s = (stage || 'system') as keyof typeof STAGE_BADGE_STYLES;
  return (
    <span
      className={`inline-block w-1.5 h-1.5 rounded-full ${
        s === 'analyst' ? 'bg-purple-400'
          : s === 'auditor' ? 'bg-blue-400'
          : 'bg-white/40'
      }`}
    />
  );
}

function StateIcon({ state }: { state: TimelineRow['state'] }) {
  switch (state) {
    case 'pending':
      return <Loader2 className="w-3.5 h-3.5 text-white/50 animate-spin" />;
    case 'error':
      return <XCircle className="w-3.5 h-3.5 text-red-400" />;
    case 'done':
    default:
      return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />;
  }
}

// ── Main component ───────────────────────────────────────────────

export function LiveStreamTimeline({
  events,
  status,
}: {
  events: StreamEvent[];
  status: StreamStatus;
}) {
  const t     = useTranslations('claim.stream');
  const tTool = useTranslations('claim.stream.tools');

  const rows = foldRows(events, t, tTool);
  const isThinking = status === 'connecting' || status === 'streaming';
  const isDone     = status === 'done';
  const isError    = status === 'error';

  return (
    <div className="space-y-3 mt-4">
      {/* Header */}
      <div className="flex items-center gap-2 text-sm">
        {isThinking && (
          <>
            <Sparkles className="w-3.5 h-3.5 text-purple-300 animate-pulse" />
            <span className="text-white/70">
              {status === 'connecting'
                ? t('connecting', { brand: AI_BRAND.name })
                : t('thinking', { brand: AI_BRAND.name })}
            </span>
          </>
        )}
        {isDone && (
          <>
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-emerald-300">{t('doneLabel')}</span>
          </>
        )}
        {isError && (
          <>
            <AlertTriangle className="w-3.5 h-3.5 text-red-400" />
            <span className="text-red-300">{t('errorLabel')}</span>
          </>
        )}
      </div>

      {/* Rows */}
      {rows.length > 0 && (
        <div
          className="bg-black/20 border border-white/5 rounded-lg p-3
                     max-h-72 overflow-y-auto space-y-1.5"
        >
          {rows.map((r) => (
            <div
              key={r.key}
              className="flex items-center gap-2 text-xs animate-fade-in"
            >
              <StateIcon state={r.state} />
              <StageDot stage={r.stage} />
              {r.stage && r.stage !== 'system' && (
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded border uppercase tracking-wider font-medium ${
                    STAGE_BADGE_STYLES[r.stage] ??
                    STAGE_BADGE_STYLES.system
                  }`}
                >
                  {r.stage}
                </span>
              )}
              <span className="text-white/85 truncate flex-1 min-w-0">
                {r.label}
              </span>
              {r.latency_ms != null && (
                <span className={`font-mono text-[10px] ${latencyTone(r.latency_ms)}`}>
                  {latencyBadge(r.latency_ms)}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
