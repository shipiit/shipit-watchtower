import Link from 'next/link';
import { StatusBadge } from '@/components/ui/badge';
import { EmptyState } from '@/components/ui/primitives';
import { count, duration, money, relative } from '@/lib/utils';
import type { TraceRow } from '@/lib/types';

/**
 * The compact trace list, shared by the overview and the entity pages.
 *
 * Header at `text-xs`, body at `text-xs` with a 32px row: density comes from
 * the type scale rather than from crushing the padding, which is why this
 * still reads at a glance where the same rows at 14px would not.
 */
export function TraceTable({ traces, compact = false }: {
  traces: TraceRow[];
  compact?: boolean;
}) {
  if (!traces.length) {
    return (
      <EmptyState
        title="No traces yet"
        hint="Call wt.setup() in your application and make one model call. The first trace arrives within seconds."
      />
    );
  }

  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="w-full min-w-[46rem] border-collapse text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="h-8 px-1 font-bold">Trace</th>
            <th className="h-8 px-1 font-bold">Status</th>
            {compact ? null : <th className="h-8 px-1 font-bold">User</th>}
            <th className="h-8 px-1 text-right font-bold">Obs</th>
            <th className="h-8 px-1 text-right font-bold">Latency</th>
            <th className="h-8 px-1 text-right font-bold">Tokens</th>
            <th className="h-8 px-1 text-right font-bold">Cost</th>
            <th className="h-8 px-1 text-right font-bold">When</th>
          </tr>
        </thead>
        <tbody className="text-xs">
          {traces.map((trace) => (
            <tr
              key={trace.id}
              className="border-b border-border/60 transition-colors last:border-0 hover:bg-muted"
            >
              <td className="h-8 max-w-[22rem] truncate px-1">
                <Link
                  href={`/traces/${trace.id}`}
                  className="font-bold hover:text-primary-accent focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {trace.name}
                </Link>
                <span className="ml-2 font-mono text-foreground-tertiary">
                  {trace.id.slice(0, 8)}
                </span>
              </td>
              <td className="h-8 px-1"><StatusBadge status={trace.status} /></td>
              {compact ? null : (
                <td className="h-8 max-w-[12rem] truncate px-1 text-muted-foreground">
                  {trace.user_id || '—'}
                </td>
              )}
              <td className="h-8 px-1 text-right tabular-nums text-muted-foreground">
                {count(trace.observation_count ?? 0)}
              </td>
              <td className="h-8 px-1 text-right tabular-nums">{duration(trace.duration_ms)}</td>
              <td className="h-8 px-1 text-right tabular-nums text-muted-foreground">
                {count(trace.total_tokens)}
              </td>
              <td className="h-8 px-1 text-right tabular-nums">{money(trace.total_cost)}</td>
              <td className="h-8 px-1 text-right text-foreground-tertiary">
                {relative(trace.started_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
