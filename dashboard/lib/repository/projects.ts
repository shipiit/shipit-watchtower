/**
 * Projects and the API keys scoped to them.
 *
 * A project is the unit of tenancy: what a key is issued against, and
 * what every read is filtered by.
 */

import { hashApiKey } from '@/lib/auth';
import { database, ensureSchema } from '@/lib/db';
import { randomId } from './shared';

/** The project a request writes into, created on first sight of its name. */
export async function resolveProject(name: string): Promise<string> {
  await ensureSchema();
  const db = database();
  const clean = (name || 'default').trim().slice(0, 64) || 'default';
  // Created on first use rather than requiring a console visit: a project
  // that has to exist before the first trace is a project nobody creates,
  // and the trace is lost while somebody reads the docs.
  await db.prepare(`INSERT INTO projects (id, name, created_at)
    VALUES (?, ?, unixepoch()) ON CONFLICT(name) DO NOTHING`).bind(clean, clean).run();
  const row = await db.prepare('SELECT id FROM projects WHERE name=?').bind(clean)
    .first<{ id: string }>();
  return row?.id ?? 'default';
}

/** The project an API key belongs to, or null if it is unknown or revoked. */
export async function projectForApiKey(key: string): Promise<string | null> {
  await ensureSchema();
  const db = database();
  const hash = await hashApiKey(key);
  const row = await db.prepare(
    'SELECT id, project_id FROM api_keys WHERE hash=? AND revoked_at IS NULL',
  ).bind(hash).first<{ id: string; project_id: string }>();
  if (!row) return null;
  // Recorded so an unused key is visibly unused when someone decides what to
  // revoke. Deliberately not awaited on the request path.
  db.prepare('UPDATE api_keys SET last_used_at=unixepoch() WHERE id=?')
    .bind(row.id).run().catch(() => {});
  return row.project_id;
}

export async function createApiKey(project: string, name: string) {
  // Resolve rather than trust: the caller passes a project *name*, and the
  // foreign key wants an id. Creating a key for a project that does not exist
  // yet is the normal case — you issue the key before the first trace.
  const projectId = await resolveProject(project);
  const raw = `wtk_${crypto.randomUUID().replaceAll('-', '')}${crypto.randomUUID().replaceAll('-', '')}`;
  const hash = await hashApiKey(raw);
  const id = randomId();
  await database().prepare(`INSERT INTO api_keys
    (id, project_id, name, prefix, hash, created_at)
    VALUES (?, ?, ?, ?, ?, unixepoch())`).bind(
    id, projectId, name.slice(0, 64) || 'unnamed', raw.slice(0, 12), hash,
  ).run();
  // The plaintext is returned exactly once and never stored. A key readable
  // from the database is a key that leaks with a database backup.
  return { id, key: raw, prefix: raw.slice(0, 12), name, project_id: projectId };
}

export async function listApiKeys(projectId = 'default') {
  await ensureSchema();
  const result = await database().prepare(
    `SELECT id, name, prefix, created_at, last_used_at, revoked_at
     FROM api_keys WHERE project_id=? ORDER BY created_at DESC`,
  ).bind(projectId).all();
  return result.results;
}

export async function revokeApiKey(id: string): Promise<void> {
  await ensureSchema();
  // Revoked, never deleted: the row is the only record that the key existed,
  // and "which key wrote this" stops being answerable the moment it is gone.
  await database().prepare('UPDATE api_keys SET revoked_at=unixepoch() WHERE id=?')
    .bind(id).run();
}

export async function listProjects() {
  await ensureSchema();
  const result = await database().prepare(
    `SELECT p.id, p.name, p.created_at,
       (SELECT COUNT(*) FROM traces t WHERE t.project_id = p.id) traces
     FROM projects p ORDER BY p.name`,
  ).all();
  return result.results;
}
