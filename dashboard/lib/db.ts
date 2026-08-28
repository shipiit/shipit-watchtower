import { env } from 'cloudflare:workers';
import { indexStatements, migrationStatements, tableStatements } from '@/db/schema';

type DatabaseEnv = { DB: D1Database };

let initialized: Promise<void> | null = null;

export function database(): D1Database {
  return (env as unknown as DatabaseEnv).DB;
}

export async function ensureSchema(): Promise<void> {
  if (!initialized) {
    initialized = (async () => {
      const db = database();
      await db.batch(tableStatements.map((sql) => db.prepare(sql)));
      // One at a time, and failures ignored: `ALTER TABLE ADD COLUMN` throws
      // once the column exists, which is the expected outcome on every run
      // after the first. Batching these would roll back the schema with them.
      for (const sql of migrationStatements) {
        try {
          await db.prepare(sql).run();
        } catch (error) {
          if (!String(error).includes('duplicate column name')) {
            console.warn('watcher: migration skipped', sql, String(error));
          }
        }
      }
      // Indexes last: several lead with a column the migrations above add, so
      // creating them first fails on any database that predates it — and
      // takes the table batch down with it.
      await db.batch(indexStatements.map((sql) => db.prepare(sql)));
      await db.prepare('PRAGMA optimize').run();
    })().catch((error) => {
      initialized = null;
      throw error;
    });
  }
  return initialized;
}

export function encode(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  return JSON.stringify(value);
}

export function decode<T>(value: string | null, fallback: T): T {
  if (!value) return fallback;
  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}
