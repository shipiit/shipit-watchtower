'use client';

import { useMemo, useState } from 'react';
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle,
  Tabs, TabsList, TabsTrigger,
} from '@/components/ui/primitives';
import { TimeSeriesChart, type ChartPoint } from '@/components/ui/chart';
import { duration } from '@/lib/utils';

interface Row { bucket: string; [key: string]: string | number | null }

interface Analytics {
  timeline: Row[];
  levelTimeline: Row[];
  modelTimeline: Row[];
  percentiles: Record<string, Record<string, number>>;
}

const METRICS = {
  traces: { label: 'Traces', key: 'traces', format: 'number' },
  cost: { label: 'Cost', key: 'cost', format: 'cost' },
  tokens: { label: 'Tokens', key: 'tokens', format: 'number' },
  latency: { label: 'Latency', key: 'latency', format: 'duration' },
} as const;

type MetricKey = keyof typeof METRICS;

/**
 * Volume over time, plus the per-type breakdown.
 *
 * One query already returns every metric per bucket, so switching between
 * traces, cost, tokens and latency is a re-render rather than a refetch —
 * which is what makes the toggle feel instant instead of like a page load.
 */
export function OverviewCharts({ analytics }: { analytics: unknown }) {
  const data = analytics as Analytics;
  const [metric, setMetric] = useState<MetricKey>('traces');
  const active = METRICS[metric];

  const primary = useMemo<ChartPoint[]>(
    () => (data.timeline ?? []).map((row) => ({
      bucket: String(row.bucket).slice(5),
      // Absence is only zero for additive metrics. An average latency of
      // "zero" on a day with no traffic is a claim the data does not make,
      // so it stays null and the line breaks.
      [active.label]:
        metric === 'latency' && !Number(row.latency)
          ? null
          : Number(row[active.key] ?? 0),
    })),
    [data.timeline, active.key, active.label, metric],
  );

  const byType = useMemo<ChartPoint[]>(() => {
    const buckets = new Map<string, ChartPoint>();
    for (const row of data.levelTimeline ?? []) {
      const bucket = String(row.bucket).slice(5);
      const point = buckets.get(bucket) ?? { bucket };
      point[String(row.series)] = Number(row.value ?? 0);
      buckets.set(bucket, point);
    }
    return [...buckets.values()];
  }, [data.levelTimeline]);

  const typeSeries = useMemo(
    () => [...new Set((data.levelTimeline ?? []).map((row) => String(row.series)))].slice(0, 6),
    [data.levelTimeline],
  );

  const percentiles = data.percentiles?.traces ?? {};

  return (
    <section className="grid gap-3 xl:grid-cols-[1.6fr_1fr]">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Volume</CardTitle>
            <CardDescription>Per day, across the selected range</CardDescription>
          </div>
          <Tabs value={metric} onValueChange={(value) => setMetric(value as MetricKey)}>
            <TabsList>
              {Object.entries(METRICS).map(([key, config]) => (
                <TabsTrigger key={key} value={key}>{config.label}</TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </CardHeader>
        <CardContent>
          <TimeSeriesChart
            data={primary}
            series={[active.label]}
            format={active.format}
            height={230}
          />
        </CardContent>
      </Card>

      <div className="flex flex-col gap-3">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Observation types</CardTitle>
              <CardDescription>What the agents actually did</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <TimeSeriesChart
              data={byType}
              series={typeSeries}
              format="number"
              height={132}
              emptyMessage="No observations yet."
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>Trace latency</CardTitle>
              <CardDescription>Percentiles, not the average</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-4 gap-2 text-center">
              {['p50', 'p90', 'p95', 'p99'].map((key) => (
                <div key={key} className="rounded-md border border-border p-2">
                  <dt className="text-xs text-foreground-tertiary">{key}</dt>
                  <dd className="mt-0.5 text-sm font-bold tabular-nums">
                    {percentiles[key] ? duration(Number(percentiles[key])) : '—'}
                  </dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>
      </div>
    </section>
  );
}
