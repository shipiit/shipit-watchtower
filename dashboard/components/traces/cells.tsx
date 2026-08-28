import type { ReactNode } from 'react';
import { StatusBadge } from '@/components/ui/badge';
import { ItemBadge } from '@/components/ui/item-badge';
import { count, duration, money, timestamp } from '@/lib/utils';
import type { ObservationRow, TraceRow } from '@/lib/types';
import type { Column } from './columns';

export type Row = TraceRow | ObservationRow;

function preview(value: string | null): string {
  if (!value) return '—';
  try {
    const decoded = JSON.parse(value);
    return typeof decoded === 'string' ? decoded : JSON.stringify(decoded);
  } catch {
    return value;
  }
}

const Muted = ({ children }: { children: ReactNode }) => (
  <span className="line-clamp-2 text-muted-foreground">{children}</span>
);

const Numeric = ({ children }: { children: ReactNode }) => (
  <span className="tabular-nums">{children}</span>
);

/**
 * One renderer per column, shared by the traces and observations views.
 *
 * Those two views used to carry their own copies of this chain, inline, on a
 * single 1871-character line — and they had silently diverged: `model` was in
 * the default column set but the traces branch had no case for it, so every
 * user's default view showed a column of dashes. One lookup makes that class
 * of bug structurally impossible.
 */
export function cellRenderers(isTrace: boolean): Record<Column, (row: Row) => ReactNode> {
  const trace = (row: Row) => row as TraceRow;
  const span = (row: Row) => row as ObservationRow;

  return {
    timestamp: (row) => (
      <span className="text-foreground-tertiary">{timestamp(row.started_at)}</span>
    ),
    name: (row) => (
      <span className="flex min-w-0 flex-col">
        <span className="truncate font-bold">{row.name}</span>
        <span className="truncate font-mono text-foreground-tertiary">
          {isTrace ? trace(row).id : span(row).trace_name}
        </span>
      </span>
    ),
    type: (row) => <ItemBadge type={isTrace ? 'trace' : span(row).event_type} />,
    input: (row) => <Muted>{preview(row.input_json)}</Muted>,
    output: (row) => <Muted>{preview(row.output_json)}</Muted>,
    observations: (row) => (
      <Numeric>{isTrace ? count(trace(row).observation_count ?? 0) : '—'}</Numeric>
    ),
    latency: (row) => <Numeric>{duration(row.duration_ms)}</Numeric>,
    tokens: (row) => <Numeric>{count(row.total_tokens)}</Numeric>,
    cost: (row) => <Numeric>{money(row.total_cost)}</Numeric>,
    model: (row) => (
      <span className="font-mono">{(isTrace ? null : span(row).model) || '—'}</span>
    ),
    provider: (row) => <span>{(isTrace ? null : span(row).provider) || '—'}</span>,
    user: (row) => <span className="truncate">{row.user_id || '—'}</span>,
    session: (row) => <span className="truncate">{row.session_id || '—'}</span>,
    quality: (row) => (
      <Numeric>
        {isTrace && trace(row).quality != null ? trace(row).quality?.toFixed(1) : '—'}
      </Numeric>
    ),
    status: (row) => (
      <StatusBadge
        status={
          isTrace
            ? trace(row).status
            : span(row).severity === 'ERROR' ? 'error' : span(row).trace_status
        }
      />
    ),
  };
}

export const rowHref = (row: Row, isTrace: boolean) =>
  isTrace
    ? `/traces/${(row as TraceRow).id}`
    : `/traces/${(row as ObservationRow).trace_id}?observation=${(row as ObservationRow).id}`;
