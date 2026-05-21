'use client';

/**
 * TracePanel — embedded Langfuse trace viewer (Phase D5.2).
 *
 * Collapsed by default underneath the conversación de revisión.
 * Click → lazy-fetches GET /claims/{id}/trace → builds the hierarchy
 * from the flat list using parent_id pointers → renders recursively.
 *
 * The whole component is read-only and stateless beyond {expanded,
 * loading, trace, error}. No global stores, no Langfuse SDK in the
 * browser — the orchestrator proxies the API call.
 */

import React, { useState } from 'react';
import { useTranslations } from 'next-intl';
import {
  ChevronRight,
  ChevronDown,
  RefreshCw,
  AlertTriangle,
  ExternalLink,
  Sparkles,
  Wrench,
  Activity,
  GitBranch,
  AlignLeft,
} from 'lucide-react';

import {
  getClaimTrace,
  ClaimTrace,
  TraceObservation,
} from '@/lib/api';
import { AI_BRAND } from '@/lib/ai';
import { toFriendlyError } from '@/lib/errors';
import { useSnackbar } from '@/lib/snackbar';

// ── Helpers ──────────────────────────────────────────────────────

interface TreeNode extends TraceObservation {
  children: TreeNode[];
}

/** Build a tree from Langfuse's flat list using parent_id pointers. */
function buildTree(observations: TraceObservation[]): TreeNode[] {
  const byId = new Map<string, TreeNode>();
  observations.forEach((o) =>
    byId.set(o.id, { ...o, children: [] }),
  );
  const roots: TreeNode[] = [];
  for (const node of byId.values()) {
    const parent = node.parent_id ? byId.get(node.parent_id) : null;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  // Sort each level chronologically — Langfuse doesn't guarantee order.
  const sortRec = (xs: TreeNode[]) => {
    xs.sort((a, b) => (a.started_at || '').localeCompare(b.started_at || ''));
    xs.forEach((x) => sortRec(x.children));
  };
  sortRec(roots);
  return roots;
}

function formatLatency(ms: number | null): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function latencyTone(ms: number | null): string {
  if (ms == null) return 'text-white/40 border-white/10';
  if (ms < 1000) return 'text-emerald-300 border-emerald-700/50';
  if (ms < 5000) return 'text-amber-300 border-amber-700/50';
  return 'text-red-300 border-red-700/50';
}

function formatCost(usd: string | null): string {
  if (!usd) return '';
  const n = Number(usd);
  if (Number.isNaN(n) || n === 0) return '';
  // Tiny costs: 4 decimal places. Larger: 3.
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(3)}`;
}

function typeIcon(type: string | null) {
  const cls = 'w-3.5 h-3.5 flex-shrink-0';
  switch (type) {
    case 'GENERATION':
      return <Sparkles className={`${cls} text-purple-300`} />;
    case 'EVENT':
      return <Activity className={`${cls} text-blue-300`} />;
    case 'SPAN':
    default:
      // Spans that look like tool calls get a wrench; others a generic dot.
      return <Wrench className={`${cls} text-white/40`} />;
  }
}

// ── Flat timeline row (Phase E2.3 — replay view for closed claims) ──

function TimelineRow({ obs }: { obs: TraceObservation }) {
  const t = useTranslations('claim.trace');
  const tokens =
    obs.input_tokens != null || obs.output_tokens != null
      ? t('tokensInOut', {
          input: obs.input_tokens ?? 0,
          output: obs.output_tokens ?? 0,
        })
      : null;
  const cost = formatCost(obs.cost_usd);

  return (
    <div className="flex items-center gap-2 py-1 px-2 text-sm hover:bg-white/5 rounded">
      {typeIcon(obs.type)}
      <span className="text-white/85 truncate flex-1 min-w-0">
        {obs.name || '(unnamed)'}
      </span>
      {tokens && (
        <span className="text-[11px] text-white/40 hidden sm:inline">{tokens}</span>
      )}
      {cost && <span className="text-[11px] text-emerald-300/80">{cost}</span>}
      <span
        className={`text-[11px] px-1.5 py-0.5 rounded border font-mono ${latencyTone(
          obs.latency_ms,
        )}`}
      >
        {formatLatency(obs.latency_ms)}
      </span>
    </div>
  );
}

/** Observations sorted by start time — chronological replay order. */
function chronological(observations: TraceObservation[]): TraceObservation[] {
  return [...observations].sort((a, b) =>
    (a.started_at || '').localeCompare(b.started_at || ''),
  );
}

// ── Tree node row ────────────────────────────────────────────────

function TreeRow({
  node,
  depth,
}: {
  node: TreeNode;
  depth: number;
}) {
  const t = useTranslations('claim.trace');
  // Top two levels start expanded so users see the agents immediately.
  const [open, setOpen] = useState(depth < 2);
  const hasChildren = node.children.length > 0;

  const cost = formatCost(node.cost_usd);
  const tokens =
    node.input_tokens != null || node.output_tokens != null
      ? t('tokensInOut', {
          input: node.input_tokens ?? 0,
          output: node.output_tokens ?? 0,
        })
      : null;

  return (
    <div className="text-sm">
      <div
        onClick={() => hasChildren && setOpen(!open)}
        className={`flex items-center gap-2 py-1 px-2 rounded transition-colors ${
          hasChildren ? 'hover:bg-white/5 cursor-pointer' : ''
        }`}
        style={{ paddingLeft: `${0.5 + depth * 1.25}rem` }}
      >
        {/* Expand chevron — invisible spacer when no children to keep alignment */}
        <span className="w-4 flex-shrink-0">
          {hasChildren ? (
            open ? (
              <ChevronDown className="w-4 h-4 text-white/40" />
            ) : (
              <ChevronRight className="w-4 h-4 text-white/40" />
            )
          ) : null}
        </span>

        {typeIcon(node.type)}

        <span className="text-white/85 truncate flex-1 min-w-0">
          {node.name || '(unnamed)'}
        </span>

        {tokens && (
          <span className="text-[11px] text-white/40 hidden sm:inline">
            {tokens}
          </span>
        )}

        {cost && (
          <span className="text-[11px] text-emerald-300/80">{cost}</span>
        )}

        <span
          className={`text-[11px] px-1.5 py-0.5 rounded border font-mono ${latencyTone(
            node.latency_ms,
          )}`}
        >
          {formatLatency(node.latency_ms)}
        </span>
      </div>

      {open && hasChildren && (
        <div className="border-l border-white/5 ml-4">
          {node.children.map((child) => (
            <TreeRow key={child.id} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main panel ───────────────────────────────────────────────────

export function TracePanel({ claimId }: { claimId: number }) {
  const t = useTranslations('claim.trace');
  const tErr = useTranslations('errors');
  const { notify } = useSnackbar();
  const [expanded, setExpanded] = useState(false);
  const [trace, setTrace] = useState<ClaimTrace | null>(null);
  const [loading, setLoading] = useState(false);
  const [emptyReason, setEmptyReason] = useState<string | null>(null);
  // E2.3 — view-mode toggle. Tree (default) is the hierarchical
  // explorer from D5.2; timeline replays observations chronologically
  // — useful when the adjuster wants to scan "what Lía did" linearly
  // long after the live SubmitModal stream is gone.
  const [view, setView] = useState<'tree' | 'timeline'>('tree');

  const toggle = async () => {
    // Collapse is free; expand may trigger a fetch.
    if (expanded) {
      setExpanded(false);
      return;
    }
    setExpanded(true);
    if (trace || loading) return; // already fetched / fetching

    setLoading(true);
    setEmptyReason(null);
    try {
      const data = await getClaimTrace(claimId);
      setTrace(data);
    } catch (err) {
      // 404 = no trace stored for this claim. Show empty state, not toast.
      const fe = toFriendlyError(err, { t: tErr });
      // Heuristic: if the backend's detail mentions "trace" or "before",
      // treat as a soft empty rather than an error. Otherwise toast.
      const looksEmpty =
        fe.message?.toLowerCase().includes('trace') ||
        fe.message?.toLowerCase().includes('before') ||
        fe.message?.toLowerCase().includes('antes');
      if (looksEmpty) {
        setEmptyReason(t('empty', { brand: AI_BRAND.name }));
      } else {
        notify({ tone: fe.tone, title: fe.title, message: fe.message });
        setEmptyReason(fe.message);
      }
    } finally {
      setLoading(false);
    }
  };

  // ── Render ───────────────────────────────────────────────────

  const stepCount = trace?.observations?.length ?? 0;
  const latency = trace?.total_latency_ms ?? null;
  const cost = formatCost(trace?.total_cost_usd ?? null);

  return (
    <div className="border-t border-white/5 pt-4 mt-4">
      <button
        onClick={toggle}
        className="w-full flex items-center justify-between gap-3 text-left group"
      >
        <div className="flex items-center gap-2">
          {expanded ? (
            <ChevronDown className="w-4 h-4 text-white/40 group-hover:text-white/70 transition-colors" />
          ) : (
            <ChevronRight className="w-4 h-4 text-white/40 group-hover:text-white/70 transition-colors" />
          )}
          <span className="text-sm font-semibold text-white/80 group-hover:text-white transition-colors">
            {expanded ? t('collapse') : t('expand')}
          </span>
        </div>

        {/* Summary chip shown only when we have data */}
        {trace && (
          <span className="text-xs text-white/40 font-mono">
            {t('summary', {
              steps: stepCount,
              latency: formatLatency(latency),
              cost: cost || '—',
            })}
          </span>
        )}
      </button>

      {expanded && (
        <div className="mt-4 space-y-3">
          {loading && (
            <div className="flex items-center gap-2 text-white/40 text-sm">
              <RefreshCw className="w-4 h-4 animate-spin" />
              <span>{t('loading', { brand: AI_BRAND.name })}</span>
            </div>
          )}

          {!loading && emptyReason && (
            <div className="flex items-start gap-2 text-white/50 text-sm bg-white/[0.03] border border-white/5 rounded-lg px-3 py-2">
              <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5 text-amber-400/70" />
              <span>{emptyReason}</span>
            </div>
          )}

          {!loading && trace && (
            <>
              {/* Header — totals + view toggle + deep-link */}
              <div className="flex items-center justify-between flex-wrap gap-3 text-xs">
                <div className="flex items-center gap-3 text-white/50">
                  <span className="text-white/30">
                    {t('summary', {
                      steps: stepCount,
                      latency: formatLatency(latency),
                      cost: cost || '—',
                    })}
                  </span>
                </div>

                <div className="flex items-center gap-3">
                  {/* E2.3 — view toggle */}
                  <div
                    className="inline-flex rounded-md border border-white/10 bg-white/[0.03] overflow-hidden"
                    role="tablist"
                    aria-label="trace view"
                  >
                    <button
                      onClick={() => setView('tree')}
                      role="tab"
                      aria-selected={view === 'tree'}
                      className={`flex items-center gap-1.5 px-2 py-1 transition-colors ${
                        view === 'tree'
                          ? 'bg-white/10 text-white/90'
                          : 'text-white/50 hover:text-white/80'
                      }`}
                    >
                      <GitBranch className="w-3 h-3" />
                      {t('viewTree')}
                    </button>
                    <button
                      onClick={() => setView('timeline')}
                      role="tab"
                      aria-selected={view === 'timeline'}
                      className={`flex items-center gap-1.5 px-2 py-1 transition-colors border-l border-white/10 ${
                        view === 'timeline'
                          ? 'bg-white/10 text-white/90'
                          : 'text-white/50 hover:text-white/80'
                      }`}
                    >
                      <AlignLeft className="w-3 h-3" />
                      {t('viewTimeline')}
                    </button>
                  </div>

                  {trace.langfuse_url && (
                    <a
                      href={trace.langfuse_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 text-purple-300 hover:text-purple-200 transition-colors"
                    >
                      <ExternalLink className="w-3 h-3" />
                      {t('openInLangfuse')}
                    </a>
                  )}
                </div>
              </div>

              {/* Body — tree or timeline, same data */}
              <div className="bg-black/20 border border-white/5 rounded-lg p-2 max-h-[28rem] overflow-y-auto">
                {view === 'tree' ? (
                  buildTree(trace.observations).map((node) => (
                    <TreeRow key={node.id} node={node} depth={0} />
                  ))
                ) : trace.observations.length === 0 ? (
                  <p className="text-xs text-white/40 px-2 py-1">
                    {t('timelineEmpty')}
                  </p>
                ) : (
                  chronological(trace.observations).map((obs) => (
                    <TimelineRow key={obs.id} obs={obs} />
                  ))
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
