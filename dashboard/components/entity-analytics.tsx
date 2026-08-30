"use client";

import { Activity, Bot, Coins, Gauge } from "lucide-react";
import { TimeSeriesChart } from "@/components/ui/chart";
import { count, duration, money } from "@/lib/utils";

type Row = Record<string, unknown>;

export function EntityAnalytics({
  timeline,
  models,
  scores = [],
  types = [],
}: {
  timeline: Row[];
  models: Row[];
  scores?: Row[];
  types?: Row[];
}) {
  // One row per bucket with a column per series, which is the shape the
  // shared chart takes. The queries return a row per (bucket, series).
  const traceRows = timeline.map((row) => ({
    bucket: String(row.bucket).slice(5),
    Traces: Number(row.value ?? row.traces ?? 0),
  }));
  const costRows = timeline.map((row) => ({
    bucket: String(row.bucket).slice(5),
    Cost: Number(row.cost ?? 0),
  }));
  const maxType = Math.max(...types.map((row) => Number(row.value)), 1);
  return (
    <section className="entity-analytics">
      <article className="panel rich-chart">
        <div className="panel-title">
          <div>
            <h2>Activity over time</h2>
            <p>Real trace volume for this entity</p>
          </div>
          <Activity size={16} />
        </div>
        <TimeSeriesChart
          data={traceRows}
          series={["Traces"]}
          format="number"
          height={190}
        />
      </article>
      <article className="panel rich-chart">
        <div className="panel-title">
          <div>
            <h2>Cost over time</h2>
            <p>Persisted generation spend</p>
          </div>
          <Coins size={16} />
        </div>
        <TimeSeriesChart
          data={costRows}
          series={["Cost"]}
          format="money"
          height={190}
        />
      </article>
      <article className="panel entity-breakdown">
        <div className="panel-title">
          <div>
            <h2>Model usage</h2>
            <p>Calls, tokens, cost, and latency</p>
          </div>
          <Bot size={16} />
        </div>
        {models.length ? (
          <div>
            {models.map((row) => (
              <span key={String(row.label)}>
                <strong>{String(row.label)}</strong>
                <small>
                  {count(Number(row.calls))} calls ·{" "}
                  {count(Number(row.tokens))} tokens
                </small>
                <b>
                  {money(Number(row.cost))}
                  <em>{duration(Number(row.latency))}</em>
                </b>
              </span>
            ))}
          </div>
        ) : (
          <div className="chart-empty">No model generations recorded.</div>
        )}
      </article>
      <article className="panel entity-breakdown">
        <div className="panel-title">
          <div>
            <h2>{types.length ? "Observation mix" : "Quality signals"}</h2>
            <p>
              {types.length
                ? "Agent workflow composition"
                : "Evaluation averages by source"}
            </p>
          </div>
          <Gauge size={16} />
        </div>
        {types.length ? (
          <div>
            {types.map((row) => (
              <span key={String(row.label)}>
                <strong>{String(row.label)}</strong>
                <i>
                  <em
                    style={{ width: `${(Number(row.value) / maxType) * 100}%` }}
                  />
                </i>
                <b>{count(Number(row.value))}</b>
              </span>
            ))}
          </div>
        ) : scores.length ? (
          <div>
            {scores.map((row) => (
              <span key={`${row.label}/${row.source}`}>
                <strong>{String(row.label)}</strong>
                <small>
                  {String(row.source)} · {count(Number(row.count))} scores
                </small>
                <b>{Number(row.average).toFixed(2)}</b>
              </span>
            ))}
          </div>
        ) : (
          <div className="chart-empty">No evaluation scores recorded.</div>
        )}
      </article>
    </section>
  );
}
