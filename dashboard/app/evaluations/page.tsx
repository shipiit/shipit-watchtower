import { Activity, CheckCircle2, Gauge, ListChecks } from "lucide-react";
import { EmptyState } from "@/components/empty-state";
import { integer } from "@/components/format";
import { PageHeader } from "@/components/page-header";
import { TimeSeriesChart } from "@/components/ui/chart";
import {
  evaluationAnalytics,
  listScores,
  scoreSummary,
} from "@/lib/repository";

export const dynamic = "force-dynamic";

interface Summary {
  name: string;
  source: string;
  count: number;
  average: number;
  minimum: number;
  maximum: number;
}
interface Score {
  id: string;
  trace_id: string;
  observation_id: string | null;
  trace_name: string;
  name: string;
  value: number | null;
  text_value: string | null;
  source: string;
  comment: string | null;
  target_type: string;
  created_at: number;
}

export default async function EvaluationsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  const filters = {
    name: typeof query.name === "string" ? query.name : undefined,
    source: typeof query.source === "string" ? query.source : undefined,
    target: typeof query.target === "string" ? query.target : undefined,
  };
  const [scores, summaries, analytics] = (await Promise.all([
    listScores(filters),
    scoreSummary(filters),
    evaluationAnalytics(filters),
  ])) as unknown as [
    Score[],
    Summary[],
    Awaited<ReturnType<typeof evaluationAnalytics>>,
  ];
  const coverage = analytics.coverage as Record<string, number>;
  const coverageRate = Number(coverage.total_traces)
    ? (Number(coverage.scored_traces) / Number(coverage.total_traces)) * 100
    : 0;
  const series = [...new Set(analytics.trend.map((row) => String(row.series)))];
  // Reshaped to one row per bucket with a column per series, which is what
  // the shared chart takes — the query returns one row per (bucket, series).
  const scoreTrend = [...analytics.trend.reduce((buckets, row) => {
    const bucket = String(row.bucket).slice(5);
    const point = buckets.get(bucket) ?? { bucket };
    point[String(row.series)] = Number(row.value ?? 0);
    return buckets.set(bucket, point);
  }, new Map<string, { bucket: string; [series: string]: string | number | null }>()).values()];
  return (
    <>
      <PageHeader
        eyebrow="Improve"
        title="Evaluations"
        description="Human feedback, programmatic checks, and LLM judges—linked to the exact trace or observation they scored."
      />
      <form className="evaluation-filters">
        <label>
          Evaluator
          <select name="name" defaultValue={filters.name || ""}>
            <option value="">All evaluators</option>
            {analytics.facets.names.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <label>
          Source
          <select name="source" defaultValue={filters.source || ""}>
            <option value="">All sources</option>
            {analytics.facets.sources.map((source) => (
              <option key={source}>{source}</option>
            ))}
          </select>
        </label>
        <label>
          Target
          <select name="target" defaultValue={filters.target || ""}>
            <option value="">All targets</option>
            <option value="trace">Trace</option>
            <option value="observation">Observation</option>
          </select>
        </label>
        <button className="primary-button" type="submit">
          Apply filters
        </button>
        <a href="/evaluations">Reset</a>
      </form>
      <section className="evaluation-kpis">
        <div>
          <Activity />
          <span>
            Total scores<strong>{integer(Number(coverage.scores))}</strong>
          </span>
        </div>
        <div>
          <ListChecks />
          <span>
            Evaluators<strong>{integer(Number(coverage.evaluators))}</strong>
          </span>
        </div>
        <div>
          <CheckCircle2 />
          <span>
            Trace coverage<strong>{coverageRate.toFixed(1)}%</strong>
          </span>
        </div>
        <div>
          <Gauge />
          <span>
            Trace / observation
            <strong>
              {integer(Number(coverage.trace_scores))} /{" "}
              {integer(Number(coverage.observation_scores))}
            </strong>
          </span>
        </div>
      </section>
      {summaries.length ? (
        <>
          <section className="panel evaluation-trend">
            <div className="panel-title">
              <div>
                <h2>Score trends</h2>
                <p>Daily averages from persisted evaluation records</p>
              </div>
            </div>
            <TimeSeriesChart data={scoreTrend} series={series} format="score" height={200} emptyMessage="No scores in this range." />
          </section>
          <section className="evaluation-grid">
            <article className="panel">
              <div className="panel-title">
                <div>
                  <h2>Score analytics</h2>
                  <p>Range, average, source, and volume</p>
                </div>
              </div>
              <div className="score-summary">
                {summaries.map((summary) => (
                  <div key={`${summary.name}/${summary.source}`}>
                    <span>
                      <strong>{summary.name}</strong>
                      <small>
                        {summary.source} · {summary.count} scores ·{" "}
                        {summary.minimum.toFixed(2)}–
                        {summary.maximum.toFixed(2)}
                      </small>
                    </span>
                    <b>{summary.average.toFixed(2)}</b>
                    <i>
                      <em
                        style={{
                          width: `${Math.max(0, Math.min(100, summary.average * 100))}%`,
                        }}
                      />
                    </i>
                  </div>
                ))}
              </div>
            </article>
            <article className="panel">
              <div className="panel-title">
                <div>
                  <h2>Latest scores</h2>
                  <p>Open the exact scored telemetry</p>
                </div>
              </div>
              <div className="score-feed">
                {scores.slice(0, 16).map((score) => (
                  <a
                    href={`/traces/${score.trace_id}${score.observation_id ? `?observation=${score.observation_id}` : ""}`}
                    key={score.id}
                  >
                    <span>
                      <strong>{score.name}</strong>
                      <small>
                        {score.trace_name} · {score.source} ·{" "}
                        {score.target_type}
                      </small>
                      {score.comment && <em>{score.comment}</em>}
                    </span>
                    <b>{score.value ?? score.text_value}</b>
                  </a>
                ))}
              </div>
            </article>
          </section>
        </>
      ) : (
        <EmptyState title="No evaluation scores match these filters" />
      )}
    </>
  );
}
