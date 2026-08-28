'use client';

import { Network } from 'lucide-react';
import { ItemIcon } from '@/components/ui/item-badge';
import { cn, duration, money } from '@/lib/utils';
import type { TraceDetail, TraceSpan } from '@/lib/types';
import { depthOf } from './tree';

interface Props {
  trace: TraceDetail;
  aggregated: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

/**
 * The execution graph.
 *
 * Aggregated collapses repeats of the same step into one row with a count,
 * which is what makes a fifty-iteration agent loop readable. Expanded shows
 * every observation — the view you want when one of those iterations is the
 * one that went wrong.
 */
export function GraphView({ trace, aggregated, selectedId, onSelect }: Props) {
  const byId = new Map(trace.spans.map((span) => [span.id, span]));
  const rows = aggregated ? aggregate(trace.spans) : trace.spans.map((span) => ({ span, count: 1 }));

  return (
    <div className="flex flex-col gap-1 p-3">
      <div className="flex items-center gap-2 rounded-md border border-border bg-muted px-2.5 py-2">
        <Network size={14} className="text-dark-green" />
        <span className="flex min-w-0 flex-col">
          <strong className="truncate text-xs">{trace.name}</strong>
          <small className="text-xs text-foreground-tertiary">
            {trace.spans.length} observations
          </small>
        </span>
        <b className="ml-auto text-xs tabular-nums">{duration(trace.duration_ms)}</b>
      </div>

      {rows.map(({ span, count }) => (
        <div key={`${span.id}:${count}`} style={{ paddingLeft: depthOf(span, byId) * 18 }}>
          <GraphNode
            span={span}
            count={count}
            selected={selectedId === span.id}
            onClick={() => onSelect(span.id)}
          />
        </div>
      ))}
    </div>
  );
}

function GraphNode({ span, count, selected, onClick }: {
  span: TraceSpan;
  count: number;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex w-full items-center gap-2 rounded-md border px-2.5 py-1.5 text-left transition-colors',
        'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring',
        selected
          ? 'border-primary-accent bg-muted'
          : 'border-border hover:bg-muted',
        span.severity === 'ERROR' && 'border-dark-red/40',
      )}
    >
      <ItemIcon type={span.event_type} />
      <span className="flex min-w-0 flex-col">
        <strong className="truncate text-xs">{span.name}</strong>
        <small className="truncate text-xs text-foreground-tertiary">
          {count > 1
            ? `${count} calls · aggregated`
            : span.model || span.provider || span.event_type.replaceAll('_', ' ')}
        </small>
      </span>
      <b className="ml-auto shrink-0 text-xs tabular-nums">
        {span.total_cost ? money(span.total_cost) : duration(span.duration_ms)}
      </b>
    </button>
  );
}

/** Group by type and name, summing the numbers that are worth summing. */
function aggregate(spans: TraceSpan[]) {
  const groups = new Map<string, TraceSpan[]>();
  for (const span of spans) {
    const key = `${span.event_type}:${span.name}`;
    groups.set(key, [...(groups.get(key) ?? []), span]);
  }
  return [...groups.values()].map((members) => ({
    span: {
      ...members[0],
      duration_ms: members.reduce((sum, item) => sum + item.duration_ms, 0),
      total_cost: members.reduce((sum, item) => sum + item.total_cost, 0),
    },
    count: members.length,
  }));
}
