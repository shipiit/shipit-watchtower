/** Evaluation scores, their aggregates, and policy decisions. */

import { database, decode, ensureSchema } from '@/lib/db';

export interface ScoreFilters { name?: string; source?: string; target?: string }

function scoreWhere(filters: ScoreFilters, values: unknown[]) {
  const clauses: string[] = [];
  if (filters.name) { clauses.push('scores.name=?'); values.push(filters.name); }
  if (filters.source) { clauses.push('scores.source=?'); values.push(filters.source); }
  if (filters.target === 'observation') clauses.push('scores.observation_id IS NOT NULL');
  if (filters.target === 'trace') clauses.push('scores.observation_id IS NULL');
  return clauses.length ? `WHERE ${clauses.join(' AND ')}` : '';
}

export async function listScores(filters: ScoreFilters = {}) {
  await ensureSchema();
  const values: unknown[] = [];
  const where = scoreWhere(filters, values);
  const result = await database().prepare(`SELECT scores.*, traces.name trace_name,
    traces.user_id, traces.session_id,
    CASE WHEN scores.observation_id IS NULL THEN 'trace' ELSE 'observation' END target_type
    FROM scores JOIN traces ON traces.id = scores.trace_id ${where}
    ORDER BY scores.created_at DESC LIMIT 200`).bind(...values).all();
  return result.results;
}

export async function scoreSummary(filters: ScoreFilters = {}) {
  await ensureSchema();
  const values: unknown[] = [];
  const where = scoreWhere(filters, values);
  const result = await database().prepare(`SELECT name, source, COUNT(*) count,
    AVG(value) average, MIN(value) minimum, MAX(value) maximum
    FROM scores ${where}${where ? ' AND' : ' WHERE'} value IS NOT NULL
    GROUP BY name, source ORDER BY count DESC`).bind(...values).all();
  return result.results;
}

export async function evaluationAnalytics(filters: ScoreFilters = {}) {
  await ensureSchema();
  const db = database();
  const values: unknown[] = [];
  const where = scoreWhere(filters, values);
  const [trend, facets, coverage] = await Promise.all([
    db.prepare(`SELECT strftime('%Y-%m-%d', scores.created_at, 'unixepoch') bucket,
      scores.name series, AVG(scores.value) value, COUNT(*) count FROM scores
      ${where}${where ? ' AND' : ' WHERE'} scores.value IS NOT NULL
      GROUP BY bucket, scores.name ORDER BY bucket`).bind(...values).all(),
    db.prepare(`SELECT
      (SELECT json_group_array(name) FROM (SELECT DISTINCT name FROM scores ORDER BY name)) names,
      (SELECT json_group_array(source) FROM (SELECT DISTINCT source FROM scores ORDER BY source)) sources`).first(),
    db.prepare(`SELECT COUNT(DISTINCT traces.id) total_traces,
      COUNT(DISTINCT scores.trace_id) scored_traces,
      COUNT(scores.id) scores,
      COUNT(DISTINCT scores.name) evaluators,
      SUM(CASE WHEN scores.observation_id IS NOT NULL THEN 1 ELSE 0 END) observation_scores,
      SUM(CASE WHEN scores.observation_id IS NULL THEN 1 ELSE 0 END) trace_scores
      FROM traces LEFT JOIN scores ON scores.trace_id=traces.id`).first(),
  ]);
  return {
    trend: trend.results,
    facets: {
      names: decode<string[]>(String(facets?.names ?? '[]'), []),
      sources: decode<string[]>(String(facets?.sources ?? '[]'), []),
    },
    coverage,
  };
}

export async function listPolicies() {
  await ensureSchema();
  const result = await database().prepare(`SELECT spans.*, traces.user_id,
    traces.session_id FROM spans JOIN traces ON traces.id=spans.trace_id
    WHERE event_type='policy' ORDER BY spans.started_at DESC LIMIT 200`).all();
  return result.results;
}
