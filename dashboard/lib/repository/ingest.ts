/**
 * Writes.
 *
 * The one rule here: a trace can arrive in more than one piece, so the
 * parent row is recomputed from the spans table rather than taken from
 * whichever bundle happened to arrive last.
 */

import { database, encode, ensureSchema } from '@/lib/db';
import type { IngestEvent, IngestTrace } from '@/lib/types';

const EVENT_COLUMNS = new Set([
  'id', 'parent_id', 'name', 'event_type', 'severity', 'status_message',
  'input', 'output', 'started_at', 'ended_at', 'duration_ms', 'model', 'provider',
  'prompt_tokens', 'completion_tokens', 'total_tokens', 'total_cost',
]);

function eventMetadata(event: IngestEvent) {
  return Object.fromEntries(Object.entries(event).filter(([key]) => !EVENT_COLUMNS.has(key)));
}

/** The project a request writes into, created on first sight of its name. */

export async function ingestTrace(trace: IngestTrace, projectId = 'default'): Promise<void> {
  await ensureSchema();
  const db = database();
  const context = trace.context ?? {};
  const metadata = trace.metadata ?? {};
  const now = Date.now() / 1000;
  const startedAt = trace.events.length
    ? Math.min(...trace.events.map((event) => event.started_at)) : now;
  const endedAt = trace.events.length
    ? Math.max(...trace.events.map((event) => event.ended_at ?? event.started_at)) : now;
  const status = trace.events.some((event) => event.severity === 'ERROR') ? 'error'
    : trace.events.some((event) => event.severity === 'WARNING') ? 'warning' : 'success';
  const totalCost = trace.events.reduce((sum, event) => sum + Number(event.total_cost ?? 0), 0);
  const totalTokens = trace.events.reduce((sum, event) => sum + Number(event.total_tokens ?? 0), 0);
  const statements = [
    db.prepare(`INSERT INTO traces (
      id, project_id, name, user_id, session_id, company_id, cost_center, channel, status,
      input_json, output_json, metadata_json, duration_ms, total_tokens,
      total_cost, quality, started_at, ended_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
      output_json=COALESCE(excluded.output_json, traces.output_json),
      metadata_json=excluded.metadata_json,
      duration_ms=MAX(excluded.duration_ms, traces.duration_ms),
      ended_at=MAX(excluded.ended_at, traces.ended_at)`).bind(
      trace.trace_id, projectId, trace.name, context.user_id ?? null, context.session_id ?? null,
      context.company_id ?? null, context.cost_center ?? null, context.channel ?? null,
      status, encode(trace.input), encode(trace.output), encode({
        ...((context.metadata as Record<string, unknown> | undefined) ?? {}),
        ...metadata,
        tags: context.tags ?? [],
      }) ?? '{}',
      Number(metadata.duration_ms ?? Math.max(0, (endedAt - startedAt) * 1000)),
      totalTokens, totalCost, metadata.quality ?? null, startedAt, endedAt,
    ),
    ...trace.events.map((event) => db.prepare(`INSERT INTO spans (
      id, project_id, trace_id, parent_id, name, event_type, severity, status_message,
      input_json, output_json, metadata_json, duration_ms, model, provider,
      prompt_tokens, completion_tokens, total_tokens, total_cost, started_at, ended_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET output_json=excluded.output_json,
      metadata_json=excluded.metadata_json, severity=excluded.severity,
      status_message=excluded.status_message, duration_ms=excluded.duration_ms,
      total_tokens=excluded.total_tokens, total_cost=excluded.total_cost,
      ended_at=excluded.ended_at`).bind(
      event.id, projectId, trace.trace_id, event.parent_id ?? null, event.name, event.event_type,
      event.severity ?? 'DEFAULT', event.status_message ?? null, encode(event.input),
      encode(event.output), encode(eventMetadata(event)) ?? '{}',
      // Not `?? 0`: an exporter that sends timestamps without an explicit
      // duration stored zero, silently deflating every latency average,
      // percentile and timeline bar downstream.
      Number(event.duration_ms
        ?? Math.max(0, ((event.ended_at ?? event.started_at) - event.started_at) * 1000)),
      event.model ?? null, event.provider ?? null,
      Number(event.prompt_tokens ?? 0), Number(event.completion_tokens ?? 0),
      Number(event.total_tokens ?? 0), Number(event.total_cost ?? 0),
      event.started_at, event.ended_at ?? null,
    )),
    // Totals and status are recomputed from the spans table, not taken from
    // this bundle. A trace can arrive in more than one piece — late spans
    // from a callback thread, a retried delivery — and deriving the parent
    // from `trace.events` alone let the last bundle overwrite the whole trace
    // with its own fragment: totals shrank, `ended_at` moved backwards, and
    // an errored trace flipped back to success. `quality` is deliberately
    // absent: `ingestScore` owns that column, and re-ingest used to wipe it.
    // D1 batches are transactional, so this lands with the inserts or not at all.
    db.prepare(`UPDATE traces SET
      total_cost = (SELECT COALESCE(SUM(total_cost), 0) FROM spans WHERE trace_id = ?),
      total_tokens = (SELECT COALESCE(SUM(total_tokens), 0) FROM spans WHERE trace_id = ?),
      started_at = MIN(started_at, COALESCE((SELECT MIN(started_at) FROM spans WHERE trace_id = ?), started_at)),
      ended_at = MAX(ended_at, COALESCE((SELECT MAX(COALESCE(ended_at, started_at)) FROM spans WHERE trace_id = ?), ended_at)),
      -- Derived from the span window rather than trusted from the bundle. A
      -- trace whose children span four seconds cannot honestly report 0ms,
      -- and the wall-clock figure the SDK sends is only the time the with
      -- block was open, which excludes work a callback thread reported later.
      duration_ms = MAX(duration_ms, CAST(COALESCE((
        SELECT (MAX(COALESCE(ended_at, started_at)) - MIN(started_at)) * 1000
        FROM spans WHERE trace_id = ?), 0) AS INTEGER)),
      status = CASE
        WHEN EXISTS (SELECT 1 FROM spans WHERE trace_id = ? AND severity = 'ERROR') THEN 'error'
        WHEN EXISTS (SELECT 1 FROM spans WHERE trace_id = ? AND severity = 'WARNING') THEN 'warning'
        ELSE 'success' END
      WHERE id = ?`).bind(...Array(8).fill(trace.trace_id)),
  ];
  await db.batch(statements);
}

export async function ingestScore(
  score: Record<string, unknown>,
  projectId = 'default',
): Promise<void> {
  await ensureSchema();
  const db = database();
  const numeric = typeof score.numeric_value === 'number' ? score.numeric_value : null;
  const traceId = String(score.trace_id ?? '');

  // A score routinely arrives before the trace it belongs to: the evaluator
  // runs off-thread and the trace is still in the exporter's queue. The FK is
  // ON DELETE CASCADE, so inserting blind either throws (an unhandled 500 the
  // SDK reads as permanent) or strands a row that every read path filters out
  // by joining traces. A placeholder row keeps the score, and the real bundle
  // fills it in — the upsert above only ever widens a trace, never shrinks it.
  const known = await db.prepare('SELECT 1 FROM traces WHERE id=?').bind(traceId).first();

  const statements = [];
  if (!known) {
    statements.push(db.prepare(
      `INSERT INTO traces (id, project_id, name, status, started_at, ended_at)
       VALUES (?, ?, ?, 'success', ?, ?) ON CONFLICT(id) DO NOTHING`,
    ).bind(
      traceId, projectId, 'pending', Number(score.created_at ?? Date.now() / 1000),
      Number(score.created_at ?? Date.now() / 1000),
    ));
  }
  statements.push(db.prepare(`INSERT INTO scores (
    id, project_id, trace_id, observation_id, name, value, text_value, source,
    comment, metadata_json, created_at
  ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  ON CONFLICT(id) DO UPDATE SET value=excluded.value,
    text_value=excluded.text_value, comment=excluded.comment,
    metadata_json=excluded.metadata_json`).bind(
    score.id, projectId, traceId, score.observation_id ?? null, score.name, numeric,
    numeric === null ? String(score.value ?? '') : null, score.source ?? 'programmatic',
    score.comment ?? null, encode(score.metadata ?? {}) ?? '{}',
    Number(score.created_at ?? Date.now() / 1000),
  ));
  if (numeric !== null) {
    // In the same batch as the insert. Two sequential round-trips left the
    // quality column stale whenever the second one failed.
    statements.push(db.prepare(`UPDATE traces SET quality = (
      SELECT AVG(value) * 100 FROM scores WHERE trace_id = ? AND value IS NOT NULL
    ) WHERE id = ?`).bind(traceId, traceId));
  }
  await db.batch(statements);
}
