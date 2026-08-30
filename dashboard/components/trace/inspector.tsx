'use client';

import { Network } from 'lucide-react';
import { useState } from 'react';
import { CopyButton } from '@/components/copy-button';
import { ItemIcon } from '@/components/ui/item-badge';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import type { TraceDetail, TraceSpan } from '@/lib/types';
import { InspectorStats } from './inspector-stats';
import { JsonBlock } from './json-block';

const TABS = ['overview', 'input', 'output', 'metadata', 'raw'] as const;
type Tab = (typeof TABS)[number];

export function Inspector({ trace, span }: { trace: TraceDetail; span: TraceSpan | null }) {
  const [tab, setTab] = useState<Tab>('overview');
  // Trace-level scores have no observation; an observation shows only its own.
  const scores = trace.scores.filter((score) =>
    span ? score.observation_id === span.id : !score.observation_id);

  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-border">
      <header className="flex items-start gap-2 border-b border-border p-3">
        {span ? <ItemIcon type={span.event_type} size={14} /> : <Network size={14} className="text-dark-green" />}
        <div className="min-w-0 flex-1">
          <small className="block text-xs text-foreground-tertiary">
            {span?.event_type ?? 'trace'}
          </small>
          <h3 className="truncate text-sm font-bold">{span?.name ?? trace.name}</h3>
          <code className="block truncate font-mono text-xs text-foreground-tertiary">
            {span?.id ?? trace.id}
          </code>
        </div>
        <CopyButton value={span?.id ?? trace.id} label="Copy id" />
      </header>

      <div className="border-b border-border px-3 py-2">
        <Tabs value={tab} onValueChange={(value) => setTab(value as Tab)}>
          <TabsList>
            {TABS.map((item) => (
              <TabsTrigger key={item} value={item}>{item}</TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {tab === 'overview' ? (
          <Overview trace={trace} span={span} scores={scores} />
        ) : tab === 'input' ? (
          <JsonBlock value={span?.input ?? trace.input} />
        ) : tab === 'output' ? (
          <JsonBlock value={span?.output ?? trace.output} />
        ) : tab === 'metadata' ? (
          <JsonBlock value={span?.metadata ?? trace.metadata} />
        ) : (
          <JsonBlock value={span ?? trace} />
        )}
      </div>
    </aside>
  );
}

function Overview({ trace, span, scores }: {
  trace: TraceDetail;
  span: TraceSpan | null;
  scores: TraceDetail['scores'];
}) {
  return (
    <div className="flex flex-col gap-4">
      <InspectorStats trace={trace} span={span} />

      {span?.status_message ? (
        <p className="rounded-md bg-light-red px-2 py-1.5 text-xs text-dark-red">
          {span.status_message}
        </p>
      ) : null}

      {scores.length ? (
        <section>
          <h4 className="mb-1.5 text-xs font-bold text-muted-foreground">Scores</h4>
          {scores.map((score) => (
            <div
              key={score.id}
              className="flex items-center gap-2 border-b border-border/60 py-1 text-xs last:border-0"
            >
              <span className="truncate">{score.name}</span>
              <span className="text-foreground-tertiary">{score.source}</span>
              <strong className="ml-auto tabular-nums">
                {String(score.value ?? score.text_value)}
              </strong>
            </div>
          ))}
        </section>
      ) : null}

      <Section title="Input"><JsonBlock value={span?.input ?? trace.input} /></Section>
      <Section title="Output"><JsonBlock value={span?.output ?? trace.output} /></Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h4 className="mb-1.5 text-xs font-bold text-muted-foreground">{title}</h4>
      {children}
    </section>
  );
}
