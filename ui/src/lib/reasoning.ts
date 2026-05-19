/**
 * Parse the orchestrator's `ai_reasoning` field into structured blocks.
 *
 * The backend appends tagged events to `ai_reasoning` as the claim moves
 * through the pipeline:
 *
 *   [RECOMMENDED: APPROVED] <freeform LLM notes…>
 *   [MANUAL OVERRIDE — 2026-05-17T04:41:40Z by octav.auditor]
 *     Status set to APPROVED. Reason: <human-provided reason>
 *
 * For UI display we split that string into typed blocks so each one can
 * be rendered as its own card (AI message vs. human override).
 *
 * The parser is intentionally lenient: anything we don't recognise is
 * captured as a free-text block so nothing is hidden from the adjuster.
 */

export type ReasoningBlock =
  | {
      kind: 'recommendation';
      decision: string; // APPROVED | REJECTED | MANUAL_REVIEW | …
      notes: string;
    }
  | {
      kind: 'override';
      decision: string;
      actor: string;
      timestamp: string; // ISO-8601
      reason: string;
      notes: string;
    }
  | {
      kind: 'reanalysis';
      decision: string;
      actor: string;
      timestamp: string;
      notes: string;
    }
  | {
      kind: 'note';
      text: string;
    };

const RECOMMENDED_RE =
  /\[RECOMMENDED:\s*([A-Z_]+)\]\s*([\s\S]*?)(?=\n\[|$)/g;

const OVERRIDE_RE =
  /\[MANUAL OVERRIDE\s*[—\-]\s*([0-9T:.+\-Z]+)\s+by\s+([^\]]+)\]\s*([\s\S]*?)(?=\n\[|$)/g;

const REANALYSIS_RE =
  /\[RE-ANALYSIS\s*[—\-]\s*([0-9T:.+\-Z]+)\s+by\s+([^\]]+)\]\s*([\s\S]*?)(?=\n\[|$)/g;

/** Split a single override body into `Status set to X.` + `Reason: …`. */
function splitOverrideBody(body: string): { notes: string; reason: string } {
  const reasonIdx = body.search(/\bReason:\s*/i);
  if (reasonIdx === -1) return { notes: body.trim(), reason: '' };
  return {
    notes: body.slice(0, reasonIdx).trim(),
    reason: body
      .slice(reasonIdx)
      .replace(/^\s*Reason:\s*/i, '')
      .trim(),
  };
}

/** Parse `ai_reasoning` into ordered, typed blocks. */
export function parseReasoning(raw: string | null | undefined): ReasoningBlock[] {
  if (!raw || !raw.trim()) return [];

  // Find every tagged match with its index in the source string so we can
  // preserve order across recommendation and override blocks.
  interface RawMatch {
    start: number;
    end: number;
    block: ReasoningBlock;
  }
  const matches: RawMatch[] = [];

  for (const m of raw.matchAll(RECOMMENDED_RE)) {
    matches.push({
      start: m.index ?? 0,
      end: (m.index ?? 0) + m[0].length,
      block: {
        kind: 'recommendation',
        decision: m[1].trim(),
        notes: m[2].trim(),
      },
    });
  }

  for (const m of raw.matchAll(OVERRIDE_RE)) {
    const body = splitOverrideBody(m[3]);
    const lowered = body.notes.toLowerCase();
    let decision = 'UPDATED';
    if (lowered.includes('approved')) decision = 'APPROVED';
    else if (lowered.includes('rejected')) decision = 'REJECTED';

    matches.push({
      start: m.index ?? 0,
      end: (m.index ?? 0) + m[0].length,
      block: {
        kind: 'override',
        decision,
        timestamp: m[1].trim(),
        actor: m[2].trim(),
        reason: body.reason,
        notes: body.notes,
      },
    });
  }

  for (const m of raw.matchAll(REANALYSIS_RE)) {
    // Body format: "Recommendation: APPROVED. <notes>\nRaw: {json}"
    const body = m[3].trim();
    const recoMatch = body.match(/^Recommendation:\s*([A-Z_]+)\.\s*([\s\S]*?)(?:\nRaw:|$)/);
    const decision = recoMatch?.[1]?.trim() || 'UPDATED';
    const notes = (recoMatch?.[2] ?? body).trim();

    matches.push({
      start: m.index ?? 0,
      end: (m.index ?? 0) + m[0].length,
      block: {
        kind: 'reanalysis',
        decision,
        timestamp: m[1].trim(),
        actor: m[2].trim(),
        notes,
      },
    });
  }

  matches.sort((a, b) => a.start - b.start);

  // Capture any leading/interstitial text that didn't match a tag so we
  // never silently swallow content.
  const blocks: ReasoningBlock[] = [];
  let cursor = 0;
  for (const m of matches) {
    if (m.start > cursor) {
      const gap = raw.slice(cursor, m.start).trim();
      if (gap) blocks.push({ kind: 'note', text: gap });
    }
    blocks.push(m.block);
    cursor = m.end;
  }
  if (cursor < raw.length) {
    const tail = raw.slice(cursor).trim();
    if (tail) blocks.push({ kind: 'note', text: tail });
  }

  // No structured tags at all — return the whole thing as a single note
  // so the panel still shows something useful.
  if (blocks.length === 0) blocks.push({ kind: 'note', text: raw.trim() });

  return blocks;
}
