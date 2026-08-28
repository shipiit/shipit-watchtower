import {
  Activity, ArrowRight, CircleDollarSign, Clock3, RadioTower, Sparkles,
} from 'lucide-react';
import Link from 'next/link';
import { OverviewCharts } from '@/components/overview-charts';
import { TraceTable } from '@/components/trace-table';
import { Button } from '@/components/ui/button';
import { MetricCard } from '@/components/ui/metric-card';
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle, EmptyState,
} from '@/components/ui/primitives';
import { count, duration, money, moneyCompact, relative } from '@/lib/utils';
import { dashboardAnalytics, overview } from '@/lib/repository';

export const dynamic = 'force-dynamic';

interface Summary {
  traces: number;
  success_rate: number;
  avg_latency: number;
  total_cost: number;
  total_tokens: number;
  avg_quality: number;
}

interface ModelSummary {
  model: string | null;
  provider: string | null;
  calls: number;
  cost: number;
  tokens: number;
  latency: number;
}

const RANGES: Array<[number, string]> = [[1, '24h'], [7, '7d'], [30, '30d'], [90, '90d']];

export default async function OverviewPage(
  { searchParams }: { searchParams: Promise<{ range?: string }> },
) {
  const selected = Number((await searchParams).range || 7);
  const days = RANGES.some(([value]) => value === selected) ? selected : 7;
  const [data, analytics] = await Promise.all([overview(days), dashboardAnalytics(days)]);
  // The daily buckets the charts already use, reused for the sparklines —
  // so a trend line costs no extra query.
  const timeline = (analytics.timeline ?? []) as Array<Record<string, unknown>>;
  const series = (key: string) => timeline.map((row) => Number(row[key] ?? 0));
  const summary = data.summary as unknown as Summary;
  const models = data.models as unknown as ModelSummary[];
  const traces = Number(summary?.traces ?? 0);
  const cost = Number(summary?.total_cost ?? 0);
  const successRate = Number(summary?.success_rate ?? 0);

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-bold text-foreground-tertiary">Observability</p>
          <h1 className="text-xl font-bold">Overview</h1>
        </div>
        <nav className="flex items-center gap-0.5 rounded-md border border-border bg-card p-0.5">
          {RANGES.map(([value, label]) => (
            <Link
              key={value}
              href={`/?range=${value}`}
              className={
                days === value
                  ? 'rounded-sm bg-muted px-2.5 py-1 text-xs font-bold text-foreground'
                  : 'rounded-sm px-2.5 py-1 text-xs font-bold text-muted-foreground hover:text-foreground'
              }
            >
              {label}
            </Link>
          ))}
        </nav>
      </header>

      {traces === 0 ? (
        <Card className="flex flex-wrap items-center gap-4 p-4">
          <span className="grid size-9 place-items-center rounded-md bg-muted text-primary-accent">
            <RadioTower size={18} />
          </span>
          <div className="min-w-[16rem] flex-1">
            <p className="text-sm font-bold">Connect your first application</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Two lines in your app, and traces stream in here.
            </p>
            <pre className="mt-2 overflow-x-auto rounded-md bg-surface-code p-2 font-mono text-xs text-muted-foreground">
{`import shipit_watcher as wt
wt.setup(service_name="my-app")`}
            </pre>
          </div>
          <Button variant="outline" size="sm" asChild>
            <Link href="/settings">Get an API key <ArrowRight size={13} /></Link>
          </Button>
        </Card>
      ) : null}

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Traces"
          value={count(traces)}
          detail={`${count(Number(summary?.total_tokens ?? 0))} tokens`}
          icon={Activity}
          trend={series('traces')}
        />
        <MetricCard
          label="Success rate"
          value={`${(successRate * 100).toFixed(1)}%`}
          detail={`${count(Math.round(traces * (1 - successRate)))} failed or warned`}
          icon={Sparkles}
        />
        <MetricCard
          label="Average latency"
          value={duration(Number(summary?.avg_latency ?? 0))}
          detail="Across completed traces"
          icon={Clock3}
          trend={series('latency')}
          higherIsBetter={false}
        />
        <MetricCard
          label="Model cost"
          value={moneyCompact(cost)}
          detail={traces ? `${money(cost / traces)} per trace` : 'No spend recorded'}
          icon={CircleDollarSign}
          trend={series('cost')}
          higherIsBetter={false}
        />
      </section>

      <OverviewCharts analytics={analytics} />

      <section className="grid gap-3 xl:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Models</CardTitle>
              <CardDescription>Usage, latency and spend by model</CardDescription>
            </div>
            <Button variant="ghost" size="sm" asChild>
              <Link href="/models">Explore <ArrowRight size={13} /></Link>
            </Button>
          </CardHeader>
          <CardContent>
            {models.length ? (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs text-muted-foreground">
                    <th className="h-8 font-bold">Model</th>
                    <th className="h-8 text-right font-bold">Calls</th>
                    <th className="h-8 text-right font-bold">Tokens</th>
                    <th className="h-8 text-right font-bold">Latency</th>
                    <th className="h-8 text-right font-bold">Cost</th>
                  </tr>
                </thead>
                {/* Body drops to `text-xs`: density from the type scale, not
                    from crushing the row height. */}
                <tbody className="text-xs">
                  {models.map((model) => (
                    <tr key={`${model.provider}/${model.model}`} className="border-b border-border/60 last:border-0">
                      <td className="h-9">
                        <span className="font-mono">{model.model || 'unknown'}</span>
                        <span className="ml-2 text-foreground-tertiary">{model.provider || ''}</span>
                      </td>
                      <td className="h-9 text-right tabular-nums">{count(model.calls)}</td>
                      <td className="h-9 text-right tabular-nums">{count(model.tokens)}</td>
                      <td className="h-9 text-right tabular-nums">{duration(model.latency)}</td>
                      <td className="h-9 text-right tabular-nums">{money(model.cost)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <EmptyState
                title="No model usage yet"
                hint="A model appears here after its first generation span arrives."
              />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>Ingestion</CardTitle>
              <CardDescription>Data-plane health</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-2.5 text-xs">
              {[
                ['Schema', 'watcher.trace.v1'],
                ['Storage', 'Cloudflare D1'],
                ['Privacy', 'masked in the SDK'],
                ['Last trace', relative(
                  data.recent[0]?.started_at as number | undefined,
                )],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-foreground-tertiary">{label}</dt>
                  <dd className="mt-0.5 font-bold">{value}</dd>
                </div>
              ))}
            </dl>
            <Button variant="outline" size="sm" className="mt-4 w-full" asChild>
              <Link href="/backends">Inspect delivery <ArrowRight size={13} /></Link>
            </Button>
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Recent traces</CardTitle>
            <CardDescription>Newest first</CardDescription>
          </div>
          <Button variant="ghost" size="sm" asChild>
            <Link href="/traces">View all <ArrowRight size={13} /></Link>
          </Button>
        </CardHeader>
        <CardContent>
          <TraceTable traces={data.recent} compact />
        </CardContent>
      </Card>
    </div>
  );
}
