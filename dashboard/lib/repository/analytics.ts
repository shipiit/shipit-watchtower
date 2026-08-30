/** Aggregates for the overview, the model table and cost allocation. */

import { database, ensureSchema } from '@/lib/db';
import { listTraces } from './traces';

export async function overview(days = 7) {
  await ensureSchema();
  const db = database();
  // Windowed by the caller's range. Hardcoded to 7 days, the four metric
  // cards showed a different period from the charts directly beneath them —
  // two sets of numbers side by side, silently disagreeing.
  const window = Math.max(1, days) * 86_400;
  const [summary, models, recent] = await Promise.all([
    db.prepare(`SELECT COUNT(*) traces,
      COALESCE(AVG(CASE WHEN status='success' THEN 1.0 ELSE 0.0 END),0) success_rate,
      COALESCE(AVG(duration_ms),0) avg_latency,
      COALESCE(SUM(total_cost),0) total_cost,
      COALESCE(SUM(total_tokens),0) total_tokens,
      COALESCE(AVG(quality),0) avg_quality
      FROM traces WHERE started_at >= unixepoch() - ?`).bind(window).first(),
    db.prepare(`SELECT model, provider, COUNT(*) calls, SUM(total_cost) cost,
      SUM(total_tokens) tokens, AVG(duration_ms) latency
      FROM spans WHERE event_type='generation' AND started_at >= unixepoch() - ?
      GROUP BY model, provider ORDER BY cost DESC LIMIT 8`).bind(window).all(),
    listTraces(10),
  ]);
  return { summary, models: models.results, recent };
}

export async function dashboardHealth() {
  await ensureSchema();
  return database().prepare(`SELECT COUNT(*) traces, MAX(received_at) last_received
    FROM traces`).first<{ traces: number; last_received: string | null }>();
}

function percentiles(values: number[]) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  const at = (fraction: number) => sorted.length
    ? sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * fraction) - 1)] : 0;
  return { p50: at(.5), p75: at(.75), p90: at(.9), p95: at(.95), p99: at(.99) };
}

export async function dashboardAnalytics(days = 7) {
  await ensureSchema();
  const seconds = Math.min(Math.max(days, 1), 90) * 86400;
  const db = database();
  const [timeline, levels, modelTimeline, levelTimeline, traceNames, users, scoreTrend, scoreSummary, traceLatency, generationLatency, observationLatency] = await Promise.all([
    db.prepare(`WITH RECURSIVE dates(bucket) AS (
      SELECT date('now', '-' || ? || ' days') UNION ALL
      SELECT date(bucket, '+1 day') FROM dates WHERE bucket < date('now')
    ), totals AS (SELECT strftime('%Y-%m-%d', started_at, 'unixepoch') bucket,
      COUNT(*) traces, SUM(total_cost) cost, SUM(total_tokens) tokens,
      AVG(duration_ms) latency FROM traces WHERE started_at >= unixepoch() - ? GROUP BY bucket)
      SELECT dates.bucket, COALESCE(totals.traces,0) traces, COALESCE(totals.cost,0) cost,
      COALESCE(totals.tokens,0) tokens, COALESCE(totals.latency,0) latency
      FROM dates LEFT JOIN totals USING(bucket) ORDER BY dates.bucket`).bind(days - 1, seconds).all(),
    db.prepare(`SELECT event_type label, COUNT(*) value FROM spans
      WHERE started_at >= unixepoch() - ? GROUP BY event_type ORDER BY value DESC`).bind(seconds).all(),
    db.prepare(`SELECT strftime('%Y-%m-%d', started_at, 'unixepoch') bucket,
      COALESCE(model, 'Unknown') series, SUM(total_cost) value, COUNT(*) calls
      FROM spans WHERE started_at >= unixepoch() - ? AND event_type='generation'
      GROUP BY bucket, series ORDER BY bucket`).bind(seconds).all(),
    db.prepare(`SELECT strftime('%Y-%m-%d', started_at, 'unixepoch') bucket,
      event_type series, COUNT(*) value FROM spans WHERE started_at >= unixepoch() - ?
      GROUP BY bucket, event_type ORDER BY bucket`).bind(seconds).all(),
    db.prepare(`SELECT name label, COUNT(*) value, SUM(total_cost) cost
      FROM traces WHERE started_at >= unixepoch() - ? GROUP BY name
      ORDER BY value DESC LIMIT 8`).bind(seconds).all(),
    db.prepare(`SELECT COALESCE(user_id, 'Anonymous') label, COUNT(*) traces,
      SUM(total_cost) cost, SUM(total_tokens) tokens FROM traces
      WHERE started_at >= unixepoch() - ? GROUP BY user_id ORDER BY cost DESC LIMIT 12`).bind(seconds).all(),
    db.prepare(`SELECT strftime('%Y-%m-%d', created_at, 'unixepoch') bucket,
      name, AVG(value) value, COUNT(*) count FROM scores
      WHERE created_at >= unixepoch() - ? AND value IS NOT NULL
      GROUP BY bucket, name ORDER BY bucket`).bind(seconds).all(),
    db.prepare(`SELECT name label, COUNT(*) count, AVG(value) average,
      SUM(CASE WHEN value >= .5 THEN 1 ELSE 0 END) positive,
      SUM(CASE WHEN value < .5 THEN 1 ELSE 0 END) negative
      FROM scores WHERE created_at >= unixepoch() - ? AND value IS NOT NULL
      GROUP BY name ORDER BY count DESC LIMIT 8`).bind(seconds).all(),
    db.prepare(`SELECT duration_ms value FROM traces WHERE started_at >= unixepoch() - ? ORDER BY duration_ms`).bind(seconds).all<{ value: number }>(),
    db.prepare(`SELECT duration_ms value FROM spans WHERE started_at >= unixepoch() - ?
      AND event_type='generation' ORDER BY duration_ms`).bind(seconds).all<{ value: number }>(),
    db.prepare(`SELECT duration_ms value FROM spans WHERE started_at >= unixepoch() - ? ORDER BY duration_ms`).bind(seconds).all<{ value: number }>(),
  ]);
  return {
    timeline: timeline.results, levels: levels.results, modelTimeline: modelTimeline.results,
    levelTimeline: levelTimeline.results, traceNames: traceNames.results,
    users: users.results, scoreTrend: scoreTrend.results, scoreSummary: scoreSummary.results,
    percentiles: {
      traces: percentiles(traceLatency.results.map((row) => Number(row.value))),
      generations: percentiles(generationLatency.results.map((row) => Number(row.value))),
      observations: percentiles(observationLatency.results.map((row) => Number(row.value))),
    },
  };
}

export async function listModels() {
  await ensureSchema();
  const result = await database().prepare(`SELECT model, provider,
    COUNT(*) calls, SUM(prompt_tokens) prompt_tokens,
    SUM(completion_tokens) completion_tokens, SUM(total_tokens) total_tokens,
    SUM(total_cost) cost, AVG(duration_ms) latency,
    SUM(CASE WHEN severity='ERROR' THEN 1 ELSE 0 END) errors,
    MIN(started_at) first_seen, MAX(started_at) last_seen
    FROM spans WHERE event_type='generation' GROUP BY model, provider
    ORDER BY cost DESC`).all();
  return result.results;
}

export async function costBreakdown() {
  await ensureSchema();
  const db = database();
  const [models, users, costCenters] = await Promise.all([
    listModels(),
    db.prepare(`SELECT user_id label, COUNT(*) traces, SUM(total_cost) cost,
      SUM(total_tokens) tokens FROM traces WHERE user_id IS NOT NULL
      GROUP BY user_id ORDER BY cost DESC LIMIT 20`).all(),
    db.prepare(`SELECT cost_center label, COUNT(*) traces, SUM(total_cost) cost,
      SUM(total_tokens) tokens FROM traces WHERE cost_center IS NOT NULL
      GROUP BY cost_center ORDER BY cost DESC LIMIT 20`).all(),
  ]);
  return { models, users: users.results, costCenters: costCenters.results };
}
