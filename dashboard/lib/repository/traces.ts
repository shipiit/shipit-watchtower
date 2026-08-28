/** Reading traces and observations, and the filter grammar behind both. */

import { database, decode, ensureSchema } from '@/lib/db';
import type { ObservationRow, SpanRow, TraceDetail, TraceRow, TraceScore } from '@/lib/types';
import { escapeLike } from './shared';

export interface TraceFilters {
  limit?: number;
  offset?: number;
  search?: string;
  status?: string;
  user?: string;
  session?: string;
  model?: string;
  provider?: string;
  name?: string;
  traceId?: string;
  company?: string;
  costCenter?: string;
  channel?: string;
  metadata?: string;
  eventType?: string;
  since?: number;
  within?: number;
  until?: number;
  minLatency?: number;
  maxLatency?: number;
  minTokens?: number;
  maxTokens?: number;
  minCost?: number;
  maxCost?: number;
  minObservations?: number;
  maxObservations?: number;
  minQuality?: number;
}

export function traceWhere(filters: TraceFilters, values: unknown[]) {
  const clauses: string[] = [];
  if (filters.search) {
    // Built from a list rather than one long literal, so adding a searchable
    // column is one entry instead of an edit inside a 200-character string.
    const searchable = ['t.name', 't.id', 't.user_id', 't.session_id',
      't.input_json', 't.output_json'];
    clauses.push(
      `(${searchable.map((column) => `${column} LIKE ? ESCAPE '\\'`).join(' OR ')})`,
    );
    // Escaped: `%` and `_` typed into the search box are LIKE wildcards, so
    // searching for a literal `%` matched every row in the table.
    const term = `%${escapeLike(filters.search)}%`;
    values.push(...searchable.map(() => term));
  }
  const exact: Array<[keyof TraceFilters, string]> = [
    ['status', 't.status'], ['user', 't.user_id'], ['session', 't.session_id'],
    ['name', 't.name'], ['traceId', 't.id'], ['company', 't.company_id'],
    ['costCenter', 't.cost_center'], ['channel', 't.channel'],
  ];
  for (const [key, column] of exact) {
    if (filters[key]) { clauses.push(`${column} = ?`); values.push(filters[key]); }
  }
  if (filters.metadata) {
    clauses.push("t.metadata_json LIKE ? ESCAPE '\\'");
    values.push(`%${escapeLike(filters.metadata)}%`);
  }
  if (filters.within != null) { clauses.push('t.started_at >= unixepoch() - ?'); values.push(filters.within); }
  const bounds: Array<[keyof TraceFilters, string, string]> = [
    ['since', 't.started_at', '>='], ['until', 't.started_at', '<='],
    ['minLatency', 't.duration_ms', '>='], ['maxLatency', 't.duration_ms', '<='],
    ['minTokens', 't.total_tokens', '>='], ['maxTokens', 't.total_tokens', '<='],
    ['minCost', 't.total_cost', '>='], ['maxCost', 't.total_cost', '<='],
    ['minQuality', 't.quality', '>='],
  ];
  for (const [key, column, operator] of bounds) {
    if (filters[key] != null) { clauses.push(`${column} ${operator} ?`); values.push(filters[key]); }
  }
  if (filters.minObservations != null) {
    clauses.push('(SELECT COUNT(*) FROM spans observation_count WHERE observation_count.trace_id=t.id) >= ?');
    values.push(filters.minObservations);
  }
  if (filters.maxObservations != null) {
    clauses.push('(SELECT COUNT(*) FROM spans observation_count WHERE observation_count.trace_id=t.id) <= ?');
    values.push(filters.maxObservations);
  }
  if (filters.model || filters.provider || filters.eventType) {
    const spanClauses = ['s.trace_id=t.id'];
    if (filters.model) { spanClauses.push('s.model=?'); values.push(filters.model); }
    if (filters.provider) { spanClauses.push('s.provider=?'); values.push(filters.provider); }
    if (filters.eventType) { spanClauses.push('s.event_type=?'); values.push(filters.eventType); }
    clauses.push(`EXISTS (SELECT 1 FROM spans s WHERE ${spanClauses.join(' AND ')})`);
  }
  return clauses.length ? `WHERE ${clauses.join(' AND ')}` : '';
}

export async function queryTraces(filters: TraceFilters = {}) {
  await ensureSchema();
  const values: unknown[] = [];
  const where = traceWhere(filters, values);
  const limit = Math.min(Math.max(filters.limit ?? 50, 1), 200);
  const offset = Math.max(filters.offset ?? 0, 0);
  const db = database();
  const [rows, count] = await Promise.all([
    db.prepare(`SELECT t.*, (SELECT COUNT(*) FROM spans observation_count WHERE observation_count.trace_id=t.id) observation_count FROM traces t ${where} ORDER BY t.started_at DESC LIMIT ? OFFSET ?`)
      .bind(...values, limit, offset).all<TraceRow>(),
    db.prepare(`SELECT COUNT(*) total FROM traces t ${where}`).bind(...values).first<{ total: number }>(),
  ]);
  return { rows: rows.results, total: Number(count?.total ?? 0) };
}

export async function listTraces(input: number | TraceFilters = 50): Promise<TraceRow[]> {
  const filters = typeof input === 'number' ? { limit: input } : input;
  return (await queryTraces(filters)).rows;
}

export async function queryObservations(filters: TraceFilters = {}) {
  await ensureSchema();
  const values: unknown[] = [];
  const traceFilter = traceWhere(filters, values);
  const observationClauses: string[] = [];
  if (filters.model) { observationClauses.push('s.model = ?'); values.push(filters.model); }
  if (filters.provider) { observationClauses.push('s.provider = ?'); values.push(filters.provider); }
  if (filters.eventType) { observationClauses.push('s.event_type = ?'); values.push(filters.eventType); }
  const extra = observationClauses.length ? `${traceFilter ? ' AND' : 'WHERE'} ${observationClauses.join(' AND ')}` : '';
  const limit = Math.min(Math.max(filters.limit ?? 50, 1), 200);
  const offset = Math.max(filters.offset ?? 0, 0);
  const from = `FROM spans s JOIN traces t ON t.id=s.trace_id ${traceFilter}${extra}`;
  const db = database();
  const [rows, count] = await Promise.all([
    db.prepare(`SELECT s.*, t.name trace_name, t.status trace_status, t.user_id, t.session_id ${from} ORDER BY s.started_at DESC LIMIT ? OFFSET ?`)
      .bind(...values, limit, offset).all<ObservationRow>(),
    db.prepare(`SELECT COUNT(*) total ${from}`).bind(...values).first<{ total: number }>(),
  ]);
  return { rows: rows.results, total: Number(count?.total ?? 0) };
}

export async function listTraceFacets() {
  await ensureSchema();
  const db = database();
  const [models, providers, names, eventTypes] = await Promise.all([
    db.prepare(`SELECT DISTINCT model value FROM spans WHERE model IS NOT NULL ORDER BY model LIMIT 100`).all<{ value: string }>(),
    db.prepare(`SELECT DISTINCT provider value FROM spans WHERE provider IS NOT NULL ORDER BY provider LIMIT 100`).all<{ value: string }>(),
    db.prepare(`SELECT DISTINCT name value FROM traces ORDER BY name LIMIT 100`).all<{ value: string }>(),
    db.prepare(`SELECT DISTINCT event_type value FROM spans ORDER BY event_type LIMIT 100`).all<{ value: string }>(),
  ]);
  return { models: models.results, providers: providers.results, names: names.results, eventTypes: eventTypes.results };
}

export async function getTrace(id: string): Promise<TraceDetail | null> {
  await ensureSchema();
  const db = database();
  const [trace, spans, scores] = await Promise.all([
    db.prepare('SELECT * FROM traces WHERE id = ?').bind(id).first<TraceRow>(),
    db.prepare('SELECT * FROM spans WHERE trace_id = ? ORDER BY started_at').bind(id).all<SpanRow>(),
    db.prepare('SELECT * FROM scores WHERE trace_id = ? ORDER BY created_at').bind(id).all<TraceScore>(),
  ]);
  if (!trace) return null;
  return {
    ...trace,
    input: decode<unknown>(trace.input_json, null),
    output: decode<unknown>(trace.output_json, null),
    metadata: decode<Record<string, unknown>>(trace.metadata_json, {}),
    spans: spans.results.map((span) => ({
      ...span,
      input: decode<unknown>(span.input_json, null),
      output: decode<unknown>(span.output_json, null),
      metadata: decode<Record<string, unknown>>(span.metadata_json, {}),
    })),
    scores: scores.results,
  };
}
