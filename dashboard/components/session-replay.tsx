import { ArrowRight, MessageSquareText } from 'lucide-react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { duration, money, timestamp } from '@/lib/utils';
import type { TraceRow } from '@/lib/types';

/** A session, read top to bottom in the order it happened. */
export function SessionReplay({ traces }: { traces: TraceRow[] }) {
  const ordered = [...traces].sort((a, b) => a.started_at - b.started_at);

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Session replay</CardTitle>
          <CardDescription>Inputs and outputs in conversation order</CardDescription>
        </div>
        <MessageSquareText size={16} className="text-muted-foreground" />
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {ordered.map((trace, index) => (
          <Turn key={trace.id} trace={trace} position={index + 1} />
        ))}
      </CardContent>
    </Card>
  );
}

function Turn({ trace, position }: { trace: TraceRow; position: number }) {
  return (
    <article className="rounded-md border border-border">
      <header className="flex items-center gap-2 border-b border-border px-3 py-2">
        <span className="grid size-5 shrink-0 place-items-center rounded-full bg-muted text-xs font-bold">
          {position}
        </span>
        <div className="min-w-0">
          <strong className="block truncate text-xs">{trace.name}</strong>
          <small className="text-xs text-foreground-tertiary">
            {timestamp(trace.started_at)} · {duration(trace.duration_ms)} · {money(trace.total_cost)}
          </small>
        </div>
        <Button variant="ghost" size="sm" className="ml-auto" asChild>
          <Link href={`/traces/${trace.id}`}>Open <ArrowRight size={12} /></Link>
        </Button>
      </header>
      <Message label="Input" value={trace.input_json} />
      <Message label="Output" value={trace.output_json} assistant />
    </article>
  );
}

function Message({ label, value, assistant = false }: {
  label: string;
  value: string | null;
  assistant?: boolean;
}) {
  return (
    <div className={assistant ? 'bg-muted/40 px-3 py-2' : 'px-3 py-2'}>
      <b className="mb-1 block text-xs text-foreground-tertiary">{label}</b>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-xs">
        {preview(value)}
      </pre>
    </div>
  );
}

function preview(value: string | null): string {
  if (!value) return 'No content captured';
  try {
    const parsed = JSON.parse(value);
    return typeof parsed === 'string' ? parsed : JSON.stringify(parsed, null, 2);
  } catch {
    return value;
  }
}
