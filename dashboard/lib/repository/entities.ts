/**
 * Sessions and users.
 *
 * These were two near-identical thirty-line functions differing only in the
 * predicate — `session_id=?` against `user_id=?` — which meant every change
 * to the shape had to be made twice, and the "model usage by generation"
 * query existed in four places across the data layer. One aggregate builder
 * per shape, parameterised by column, keeps them honestly identical.
 */

import { database, ensureSchema } from '@/lib/db';
import { listTraces } from './traces';

type Scope = 'session_id' | 'user_id';

/** Generation spend and latency by model, for one entity. */
const modelUsage = (scope: Scope) => `
  SELECT COALESCE(s.model, 'Unknown') label, COUNT(*) calls,
    SUM(s.total_cost) cost, SUM(s.total_tokens) tokens, AVG(s.duration_ms) latency
  FROM spans s JOIN traces t ON t.id = s.trace_id
  WHERE t.${scope} = ? AND s.event_type = 'generation'
  GROUP BY s.model ORDER BY calls DESC`;

/** Trace volume bucketed over time. Sessions are minutes; users are days. */
const timeline = (scope: Scope, format: string) => `
  SELECT strftime('${format}', started_at, 'unixepoch') bucket, 'Traces' series,
    COUNT(*) value, SUM(total_cost) cost, SUM(total_tokens) tokens,
    AVG(duration_ms) latency
  FROM traces WHERE ${scope} = ? GROUP BY bucket ORDER BY bucket`;

export async function listSessions() {
  await ensureSchema();
  const result = await database().prepare(`SELECT session_id id, user_id,
    COUNT(*) traces, SUM(total_cost) cost, SUM(total_tokens) tokens,
    AVG(duration_ms) latency, AVG(quality) quality,
    MIN(started_at) started_at, MAX(ended_at) ended_at
    FROM traces WHERE session_id IS NOT NULL GROUP BY session_id, user_id
    ORDER BY ended_at DESC LIMIT 100`).all();
  return result.results;
}

export async function listUsers() {
  await ensureSchema();
  const result = await database().prepare(`SELECT user_id id, company_id,
    COUNT(*) traces, COUNT(DISTINCT session_id) sessions,
    SUM(total_cost) cost, SUM(total_tokens) tokens, AVG(quality) quality,
    MAX(ended_at) last_seen FROM traces WHERE user_id IS NOT NULL
    GROUP BY user_id, company_id ORDER BY last_seen DESC LIMIT 100`).all();
  return result.results;
}

export async function getSession(id: string) {
  await ensureSchema();
  const db = database();
  const [summary, buckets, types, models, scores] = await Promise.all([
    db.prepare(`SELECT session_id id, user_id, company_id, COUNT(*) traces,
      SUM(total_cost) cost, SUM(total_tokens) tokens, AVG(duration_ms) latency,
      AVG(quality) quality, MIN(started_at) started_at, MAX(ended_at) ended_at
      FROM traces WHERE session_id = ?
      GROUP BY session_id, user_id, company_id`).bind(id).first(),
    db.prepare(timeline('session_id', '%Y-%m-%d %H:%M')).bind(id).all(),
    db.prepare(`SELECT s.event_type label, COUNT(*) value
      FROM spans s JOIN traces t ON t.id = s.trace_id
      WHERE t.session_id = ? GROUP BY s.event_type ORDER BY value DESC`).bind(id).all(),
    db.prepare(modelUsage('session_id')).bind(id).all(),
    db.prepare(`SELECT sc.*, t.name trace_name FROM scores sc
      JOIN traces t ON t.id = sc.trace_id
      WHERE t.session_id = ? ORDER BY sc.created_at DESC`).bind(id).all(),
  ]);
  if (!summary) return null;
  return {
    summary,
    traces: await listTraces({ session: id, limit: 200 }),
    timeline: buckets.results,
    types: types.results,
    models: models.results,
    scores: scores.results,
  };
}

export async function getUser(id: string) {
  await ensureSchema();
  const db = database();
  const [summary, sessions, buckets, models, scores] = await Promise.all([
    db.prepare(`SELECT user_id id, company_id, COUNT(*) traces,
      COUNT(DISTINCT session_id) sessions, SUM(total_cost) cost,
      SUM(total_tokens) tokens, AVG(duration_ms) latency, AVG(quality) quality,
      MIN(started_at) first_seen, MAX(ended_at) last_seen
      FROM traces WHERE user_id = ? GROUP BY user_id, company_id`).bind(id).first(),
    db.prepare(`SELECT session_id id, COUNT(*) traces, SUM(total_cost) cost,
      SUM(total_tokens) tokens, MAX(ended_at) ended_at FROM traces
      WHERE user_id = ? AND session_id IS NOT NULL GROUP BY session_id
      ORDER BY ended_at DESC LIMIT 100`).bind(id).all(),
    db.prepare(timeline('user_id', '%Y-%m-%d')).bind(id).all(),
    db.prepare(modelUsage('user_id')).bind(id).all(),
    db.prepare(`SELECT sc.name label, sc.source, COUNT(*) count, AVG(sc.value) average
      FROM scores sc JOIN traces t ON t.id = sc.trace_id
      WHERE t.user_id = ? GROUP BY sc.name, sc.source ORDER BY count DESC`).bind(id).all(),
  ]);
  if (!summary) return null;
  return {
    summary,
    sessions: sessions.results,
    traces: await listTraces({ user: id, limit: 200 }),
    timeline: buckets.results,
    models: models.results,
    scores: scores.results,
  };
}
