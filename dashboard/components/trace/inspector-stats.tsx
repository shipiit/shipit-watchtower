import { StatusBadge } from '@/components/ui/badge';
import { count, duration, money, timestamp } from '@/lib/utils';
import type { TraceDetail, TraceSpan } from '@/lib/types';

/**
 * The fact table at the top of the inspector.
 *
 * Every row self-nulls: an observation with no model does not render a "Model
 * —" row, because a column of dashes teaches people to stop reading the
 * panel. What is shown is what is known.
 */
export function InspectorStats({ trace, span }: {
  trace: TraceDetail;
  span: TraceSpan | null;
}) {
  const metadata = (span?.metadata ?? {}) as Record<string, unknown>;
  const started = span?.started_at ?? trace.started_at;
  const ended = span?.ended_at ?? trace.ended_at;

  const rows: Array<[string, React.ReactNode]> = [
    ['Status', <StatusBadge key="status" status={span?.severity ?? trace.status} />],
    ['Latency', duration(span?.duration_ms ?? trace.duration_ms)],
  ];

  const ttft = Number(metadata.time_to_first_token_ms ?? 0);
  // For a streamed answer this is the latency a user actually feels; total
  // duration mostly measures how long the answer was.
  if (ttft) rows.push(['Time to first token', duration(ttft)]);

  rows.push(['Cost', money(span?.total_cost ?? trace.total_cost)]);

  if (span) {
    const input = Number(span.prompt_tokens ?? 0);
    const output = Number(span.completion_tokens ?? 0);
    rows.push([
      'Tokens',
      input || output
        ? `${count(input)} → ${count(output)} (Σ ${count(input + output)})`
        : count(span.total_tokens),
    ]);
  } else {
    rows.push(['Tokens', count(trace.total_tokens)]);
  }

  if (span?.model) rows.push(['Model', <Mono key="model">{span.model}</Mono>]);
  if (span?.provider) rows.push(['Provider', span.provider]);
  if (metadata.streamed_chunks) rows.push(['Chunks', count(Number(metadata.streamed_chunks))]);
  if (metadata.cached_tokens) rows.push(['Cached', count(Number(metadata.cached_tokens))]);
  if (metadata.prompt_name) {
    rows.push(['Prompt', <Mono key="prompt">
      {String(metadata.prompt_name)}
      {metadata.prompt_version ? ` v${metadata.prompt_version}` : ''}
    </Mono>]);
  }
  if (metadata.temperature != null) rows.push(['Temperature', String(metadata.temperature)]);

  rows.push(['Started', timestamp(started)]);
  if (ended) rows.push(['Ended', timestamp(ended)]);
  if (span?.id) rows.push(['Observation id', <Mono key="id">{span.id}</Mono>]);
  if (span?.parent_id) rows.push(['Parent', <Mono key="parent">{span.parent_id}</Mono>]);

  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
      {rows.map(([label, value], index) => (
        <div key={`${label}-${index}`} className="contents">
          <dt className="text-foreground-tertiary">{label}</dt>
          <dd className="truncate font-bold tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

const Mono = ({ children }: { children: React.ReactNode }) => (
  <span className="font-mono font-normal">{children}</span>
);
