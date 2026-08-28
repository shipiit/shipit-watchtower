/** The prompt registry: immutable versions, labels, and lookups. */

import { database, decode, encode, ensureSchema } from '@/lib/db';
import { randomId } from './shared';

/**
 * A stored row as the API returns it.
 *
 * This four-field decode was copy-pasted at three call sites, which is three
 * places to forget a field the next time one is added.
 */
function hydrate(row: Record<string, unknown>) {
  return {
    ...row,
    prompt: decode(String(row.template_json), null),
    labels: decode<string[]>(String(row.labels_json), []),
    tags: decode<string[]>(String(row.tags_json), []),
    config: decode<Record<string, unknown>>(String(row.config_json), {}),
  };
}

export async function createPrompt(payload: Record<string, unknown>) {
  await ensureSchema();
  const db = database();
  const id = randomId();
  const createdAt = Date.now() / 1000;
  // The next version number is chosen inside the INSERT. Read it first and
  // two concurrent publishes of the same prompt both compute n+1, and the
  // second violates UNIQUE(name, version) — a 500 on what is a legitimate
  // race, from a release script that may well run in parallel.
  await db.prepare(`INSERT INTO prompt_versions (id, name, version, prompt_type,
    template_json, labels_json, tags_json, config_json, commit_message, created_at)
    SELECT ?, ?, COALESCE(MAX(version),0)+1, ?, ?, ?, ?, ?, ?, ?
    FROM prompt_versions WHERE name=?`).bind(
    id, payload.name, payload.type ?? 'text', encode(payload.prompt) ?? 'null',
    encode(payload.labels ?? []) ?? '[]', encode(payload.tags ?? []) ?? '[]',
    encode(payload.config ?? {}) ?? '{}', payload.commit_message ?? null, createdAt,
    payload.name,
  ).run();
  const stored = await db.prepare('SELECT version FROM prompt_versions WHERE id=?')
    .bind(id).first<{ version: number }>();
  return {
    id,
    name: payload.name,
    version: Number(stored?.version ?? 1),
    prompt: payload.prompt,
    type: payload.type ?? 'text',
    labels: payload.labels ?? [],
    tags: payload.tags ?? [],
    config: payload.config ?? {},
    created_at: createdAt,
  };
}

export async function listPrompts() {
  await ensureSchema();
  const result = await database().prepare(`SELECT p.*, counts.versions FROM prompt_versions p
    JOIN (SELECT name, MAX(version) latest, COUNT(*) versions FROM prompt_versions GROUP BY name) counts
    ON counts.name=p.name AND counts.latest=p.version ORDER BY p.created_at DESC`).all();
  return result.results.map(hydrate);
}

export async function getPromptVersion(name: string, version?: string, label?: string) {
  await ensureSchema();
  const db = database();
  let row;
  if (version) {
    row = await db.prepare(
      'SELECT * FROM prompt_versions WHERE name=? AND version=?',
    ).bind(name, Number(version)).first();
  }
  // Exact membership, not a substring: `LIKE '%"prod"%'` also matches a prompt
  // labelled `production`, so asking for one could serve the other.
  else if (label) {
    row = await db.prepare(`SELECT * FROM prompt_versions
      WHERE name = ?
        AND EXISTS (SELECT 1 FROM json_each(labels_json) WHERE value = ?)
      ORDER BY version DESC LIMIT 1`).bind(name, label).first();
  }
  else {
    row = await db.prepare(
      'SELECT * FROM prompt_versions WHERE name=? ORDER BY version DESC LIMIT 1',
    ).bind(name).first();
  }
  if (!row) return null;
  return { ...row, prompt: decode(String(row.template_json), null), labels: decode(String(row.labels_json), []), tags: decode(String(row.tags_json), []), config: decode(String(row.config_json), {}) };
}

export async function listPromptVersions(name: string) {
  await ensureSchema();
  const result = await database().prepare(
    'SELECT * FROM prompt_versions WHERE name=? ORDER BY version DESC',
  ).bind(name).all();
  return result.results.map(hydrate);
}
