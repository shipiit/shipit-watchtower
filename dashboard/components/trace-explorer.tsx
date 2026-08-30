'use client';

import { GanttChartSquare, MessageSquareText, Network } from 'lucide-react';
import { useState } from 'react';
import { GraphView } from '@/components/trace/graph-view';
import { Inspector } from '@/components/trace/inspector';
import { MessagesView } from '@/components/trace/messages-view';
import { TimelineView } from '@/components/trace/timeline-view';
import { SplitPane } from '@/components/ui/split-pane';
import { Card, Tabs, TabsList, TabsTrigger } from '@/components/ui/primitives';
import type { TraceDetail } from '@/lib/types';

const VIEWS = [
  ['graph', 'graph', Network],
  ['timeline', 'timeline', GanttChartSquare],
  ['messages', 'messages', MessageSquareText],
] as const;

type View = (typeof VIEWS)[number][0];

/** Composition only — each view and the inspector live in their own file. */
export function TraceExplorer({ trace, initialSelectedId = null }: {
  trace: TraceDetail;
  initialSelectedId?: string | null;
}) {
  const [view, setView] = useState<View>('graph');
  const [aggregated, setAggregated] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(initialSelectedId);
  const selected = trace.spans.find((span) => span.id === selectedId) ?? null;

  return (
    <Card className="overflow-hidden">
      <header className="flex flex-wrap items-center gap-2 border-b border-border p-2">
        <Tabs value={view} onValueChange={(value) => setView(value as View)}>
          <TabsList>
            {VIEWS.map(([value, label, Icon]) => (
              <TabsTrigger key={value} value={value}>
                <Icon size={13} />{label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        {view === 'graph' ? (
          <Tabs
            value={aggregated ? 'aggregated' : 'expanded'}
            onValueChange={(value) => setAggregated(value === 'aggregated')}
          >
            <TabsList>
              <TabsTrigger value="aggregated">Aggregated</TabsTrigger>
              <TabsTrigger value="expanded">Expanded</TabsTrigger>
            </TabsList>
          </Tabs>
        ) : (
          <span className="text-xs text-muted-foreground">
            {trace.spans.length} observations
          </span>
        )}
      </header>

      {/* Draggable, arrow-key adjustable, double-click to reset — and the
          width is remembered. Below `lg` the panes stack, because a split on
          a phone gives neither side enough room to be read. */}
      <SplitPane
        storageKey="watcher.trace-split"
        defaultPercent={62}
        min={35}
        max={78}
        className="min-h-[30rem] lg:max-h-[72vh]"
        left={
          view === 'graph' ? (
            <GraphView
              trace={trace}
              aggregated={aggregated}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
          ) : view === 'timeline' ? (
            <TimelineView trace={trace} selectedId={selectedId} onSelect={setSelectedId} />
          ) : (
            <MessagesView trace={trace} onSelect={setSelectedId} />
          )
        }
        right={<Inspector trace={trace} span={selected} />}
      />
    </Card>
  );
}
