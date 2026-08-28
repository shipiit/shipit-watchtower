'use client';

import { useMemo } from 'react';
import { ItemIcon } from '@/components/ui/item-badge';
import { cn, count, duration, money } from '@/lib/utils';
import type { TraceDetail } from '@/lib/types';
import { depthOf, timingOf } from './tree';

interface Props {
  trace: TraceDetail;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

/** Every observation against the trace's own wall clock. */
export function TimelineView({ trace, selectedId, onSelect }: Props) {
  const byId = useMemo(
    () => new Map(trace.spans.map((span) => [span.id, span])),
    [trace.spans],
  );

  const timing = useMemo(() => {
    const start = Math.min(trace.started_at, ...trace.spans.map((span) => span.started_at));
    const end = Math.max(
      trace.ended_at,
      ...trace.spans.map((span) => span.ended_at ?? span.started_at),
    );
    return timingOf(trace.spans, start, end);
  }, [trace.ended_at, trace.spans, trace.started_at]);

  return (
    <div className="text-xs">
      <div className="sticky top-0 grid grid-cols-[1fr_2fr_5rem_6rem] gap-2 border-b border-border bg-card px-3 py-2 font-bold text-muted-foreground">
        <span>Observation</span>
        <span>Execution window</span>
        <span className="text-right">Latency</span>
        <span className="text-right">Usage</span>
      </div>

      {trace.spans.map((span) => {
        const position = timing.get(span.id)!;
        return (
          <button
            key={span.id}
            type="button"
            onClick={() => onSelect(span.id)}
            className={cn(
              'grid w-full grid-cols-[1fr_2fr_5rem_6rem] items-center gap-2 border-b border-border/60 px-3 py-1.5 text-left transition-colors',
              'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring',
              selectedId === span.id ? 'bg-muted' : 'hover:bg-muted',
            )}
          >
            <span
              className="flex min-w-0 items-center gap-1.5"
              style={{ paddingLeft: depthOf(span, byId) * 14 }}
            >
              <ItemIcon type={span.event_type} size={12} />
              <span className="min-w-0">
                <strong className="block truncate">{span.name}</strong>
                <small className="block truncate text-foreground-tertiary">
                  {span.model || span.provider || span.event_type.replaceAll('_', ' ')}
                </small>
              </span>
            </span>

            <span className="relative h-1.5 rounded-full bg-muted">
              <b
                className={cn(
                  'absolute inset-y-0 rounded-full',
                  span.severity === 'ERROR' ? 'bg-dark-red' : 'bg-primary-accent',
                )}
                style={{
                  left: `${position.left}%`,
                  width: `${Math.min(position.width, 100 - position.left)}%`,
                }}
              />
            </span>

            <span className="text-right tabular-nums">{duration(span.duration_ms)}</span>
            <span className="text-right tabular-nums text-muted-foreground">
              {span.total_cost ? money(span.total_cost) : `${count(span.total_tokens)} tok`}
            </span>
          </button>
        );
      })}
    </div>
  );
}
