import type { TraceSpan } from '@/lib/types';

/**
 * How deeply a span is nested.
 *
 * The `seen` set is not defensive noise: `parent_id` arrives from an
 * instrumented application, and a cycle there would otherwise recurse until
 * the stack gives out — taking the whole page with it rather than rendering
 * one odd row.
 */
export function depthOf(
  span: TraceSpan,
  byId: Map<string, TraceSpan>,
  seen = new Set<string>(),
): number {
  if (!span.parent_id || !byId.has(span.parent_id) || seen.has(span.id)) return 0;
  seen.add(span.id);
  return 1 + depthOf(byId.get(span.parent_id)!, byId, seen);
}

/** Where each span sits on the timeline, as percentages of the trace window. */
export function timingOf(spans: TraceSpan[], start: number, end: number) {
  const total = Math.max(end - start, 0.001);
  return new Map(spans.map((span) => [span.id, {
    left: Math.max(0, ((span.started_at - start) / total) * 100),
    // A floor of 2%, so a sub-millisecond span is still a visible mark
    // rather than a zero-width sliver that looks like missing data.
    width: Math.max(
      2,
      ((Math.max(span.ended_at ?? span.started_at, span.started_at) - span.started_at) / total) * 100,
    ),
  }]));
}
