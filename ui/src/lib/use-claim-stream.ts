'use client';

/**
 * useClaimStream — React hook around the orchestrator's SSE feed.
 *
 * Phase E2.1. Pair with a request_id you generated locally and sent
 * as the `X-Trace-Id` header on /claims/submit-upload (or /reanalyze).
 *
 *   const { events, status, error } = useClaimStream(requestId);
 *
 * `requestId === null` keeps the hook idle. The moment you set it,
 * the hook opens an `EventSource` (with JWT in `?token=` because the
 * browser API can't set Authorization headers) and starts appending
 * incoming events. It tears the connection down on unmount or when
 * a `pipeline.done` / `pipeline.error` event arrives.
 */

import { useEffect, useRef, useState } from 'react';

import { getToken } from './auth';

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// Mirrors the StreamEvent TypedDict on the backend (event_bus.py).
export type StreamEventType =
  | 'pipeline.start'
  | 'agent.start'
  | 'agent.end'
  | 'tool.call'
  | 'tool.result'
  | 'agent.decision'
  | 'pipeline.done'
  | 'pipeline.error';

export interface StreamEvent {
  type:       StreamEventType;
  request_id: string;
  timestamp:  string;
  stage?:     'analyst' | 'auditor' | 'system' | string;
  name?:      string;
  data?:      Record<string, unknown>;
  latency_ms?: number;
}

export type StreamStatus = 'idle' | 'connecting' | 'streaming' | 'done' | 'error';

interface UseClaimStreamResult {
  events: StreamEvent[];
  status: StreamStatus;
  error:  string | null;
}

export function useClaimStream(
  requestId: string | null,
): UseClaimStreamResult {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>('idle');
  const [error,  setError]  = useState<string | null>(null);

  // Ref to the active EventSource so we can close on rerun / unmount.
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    // No request → stay idle. Reset whatever previous state existed.
    if (!requestId) {
      sourceRef.current?.close();
      sourceRef.current = null;
      setEvents([]);
      setStatus('idle');
      setError(null);
      return;
    }

    let cancelled = false;
    setStatus('connecting');
    setEvents([]);
    setError(null);

    (async () => {
      let token = '';
      try {
        token = await getToken();
      } catch (e) {
        if (cancelled) return;
        setStatus('error');
        setError(e instanceof Error ? e.message : 'Could not obtain auth token');
        return;
      }
      if (cancelled) return;

      const url = `${API_BASE_URL}/claims/stream/${encodeURIComponent(
        requestId,
      )}?token=${encodeURIComponent(token)}`;

      const es = new EventSource(url);
      sourceRef.current = es;

      es.onopen = () => {
        if (!cancelled) setStatus('streaming');
      };

      const handle = (ev: MessageEvent) => {
        if (cancelled) return;
        try {
          const parsed: StreamEvent = JSON.parse(ev.data);
          setEvents((prev) => [...prev, parsed]);
          if (parsed.type === 'pipeline.done') {
            setStatus('done');
            es.close();
            sourceRef.current = null;
          } else if (parsed.type === 'pipeline.error') {
            setStatus('error');
            setError(
              (parsed.data?.['error'] as string | undefined) ||
                'Pipeline error',
            );
            es.close();
            sourceRef.current = null;
          }
        } catch {
          // ignore malformed payloads — backend always serialises JSON,
          // but be defensive against proxies that mangle bytes.
        }
      };

      // We attach handlers for every named event type AS WELL AS the
      // default `message` event so we don't miss anything if the
      // backend ever omits `event:`.
      const namedTypes: StreamEventType[] = [
        'pipeline.start',
        'agent.start',
        'agent.end',
        'tool.call',
        'tool.result',
        'agent.decision',
        'pipeline.done',
        'pipeline.error',
      ];
      namedTypes.forEach((t) => es.addEventListener(t, handle as EventListener));
      es.onmessage = handle;

      es.onerror = () => {
        // EventSource fires onerror both on transient disconnects and
        // on final failures. Trust the server-emitted terminal events
        // for normal completion; surface this only when we haven't
        // already wound down.
        if (cancelled) return;
        if (status === 'done' || status === 'error') return;
        // Backend closes the connection after pipeline.done — that
        // also triggers onerror. Treat as a graceful close when at
        // least one event arrived.
        setStatus((s) => (s === 'streaming' ? 'done' : 'error'));
        if (status !== 'streaming') {
          setError('Stream connection failed');
        }
        es.close();
        sourceRef.current = null;
      };
    })();

    return () => {
      cancelled = true;
      sourceRef.current?.close();
      sourceRef.current = null;
    };
    // We deliberately exclude `status` from deps — it changes inside
    // the effect and would cause re-runs that close/reopen the stream.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestId]);

  return { events, status, error };
}
