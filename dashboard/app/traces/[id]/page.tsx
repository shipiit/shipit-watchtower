import { ArrowLeft, Braces, Clock3, Coins, Hash, Radio, UserRound } from 'lucide-react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { CopyButton } from '@/components/copy-button';
import { TraceExplorer } from '@/components/trace-explorer';
import { StatusBadge } from '@/components/ui/badge';
import { StatStrip, type Stat } from '@/components/ui/stat-strip';
import { count, duration, money, relative } from '@/lib/utils';
import { getTrace } from '@/lib/repository';

export const dynamic = 'force-dynamic';

export default async function TraceDetailPage(
  { params, searchParams }: {
    params: Promise<{ id: string }>;
    searchParams: Promise<{ observation?: string }>;
  },
) {
  const { id } = await params;
  const { observation } = await searchParams;
  const trace = await getTrace(id);
  if (!trace) notFound();

  const tags = Array.isArray(trace.metadata.tags) ? trace.metadata.tags.map(String) : [];

  const stats: Stat[] = [
    { label: 'Duration', value: duration(trace.duration_ms), icon: Clock3 },
    { label: 'Cost', value: money(trace.total_cost), icon: Coins },
    { label: 'Tokens', value: count(trace.total_tokens), icon: Hash },
    { label: 'Observations', value: count(trace.spans.length), icon: Braces },
    { label: 'User', value: trace.user_id || 'anonymous', icon: UserRound },
    { label: 'Channel', value: trace.channel || '—', icon: Radio },
  ];

  const context: Stat[] = [
    {
      label: 'Session',
      value: trace.session_id
        ? <Link className="hover:text-primary-accent" href={`/sessions/${encodeURIComponent(trace.session_id)}`}>{trace.session_id}</Link>
        : '—',
    },
    { label: 'Company', value: trace.company_id || '—' },
    { label: 'Cost centre', value: trace.cost_center || '—' },
  ];

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-3">
      <Link
        href="/traces"
        className="inline-flex w-fit items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft size={13} />All traces
      </Link>

      {/* One header band instead of four. */}
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-lg font-bold">{trace.name}</h1>
            <StatusBadge status={trace.status} />
            <span className="text-xs text-foreground-tertiary">{relative(trace.started_at)}</span>
          </div>
          <div className="mt-1 flex items-center gap-1">
            <code className="font-mono text-xs text-foreground-tertiary">{trace.id}</code>
            <CopyButton value={trace.id} label="Copy trace id" />
          </div>
        </div>

        <div className="flex flex-col items-end gap-2">
          <StatStrip stats={stats} className="justify-end" />
          <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-1">
            <StatStrip stats={context} />
            {tags.length ? (
              <span className="flex flex-wrap gap-1">
                {tags.map((tag) => (
                  <span key={tag} className="rounded-md bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                    {tag}
                  </span>
                ))}
              </span>
            ) : null}
            <CopyButton
              value={JSON.stringify({
                trace_id: trace.id,
                user_id: trace.user_id,
                session_id: trace.session_id,
                company_id: trace.company_id,
                cost_center: trace.cost_center,
                channel: trace.channel,
                metadata: trace.metadata,
              }, null, 2)}
              label="Copy trace context as JSON"
            />
          </div>
        </div>
      </header>

      <TraceExplorer trace={trace} initialSelectedId={observation} />
    </div>
  );
}
