import { env } from 'cloudflare:workers';
import { apiKeyFromRequest } from '@/lib/auth';
import { projectForApiKey, resolveProject } from '@/lib/repository';

type IngestEnv = { WATCHER_INGEST_KEY?: string };

/**
 * The ingest secret, read from the Worker's own environment.
 *
 * `process.env` is the wrong source here: `lib/db.ts` reads its D1 binding
 * from `cloudflare:workers`, and whether secrets are also mirrored into
 * `process.env` depends on deployment config. Read from the wrong one and
 * `expected` is `undefined` in production, every ingest 401s, and the outage
 * looks like an SDK bug. `process.env` remains a fallback for local tooling.
 */
function ingestKey(): string | undefined {
  const fromWorker = (env as unknown as IngestEnv)?.WATCHER_INGEST_KEY;
  return fromWorker || process.env.WATCHER_INGEST_KEY || undefined;
}

type TimingSafe = { timingSafeEqual?(a: ArrayBufferView, b: ArrayBufferView): boolean };

/**
 * Constant-time compare, so the check cannot be probed byte by byte.
 *
 * Workers offers `crypto.subtle.timingSafeEqual`; it is not in the standard
 * SubtleCrypto surface, so there is a portable fallback for anywhere else
 * this runs (tests, a Node adapter). Both compare the full length regardless
 * of where the first difference is.
 */
function sameSecret(provided: string, expected: string): boolean {
  const a = new TextEncoder().encode(provided);
  const b = new TextEncoder().encode(expected);
  if (a.byteLength !== b.byteLength) return false;
  const native = (crypto.subtle as TimingSafe).timingSafeEqual;
  if (native) return native.call(crypto.subtle, a, b);
  let difference = 0;
  for (let index = 0; index < a.byteLength; index += 1) difference |= a[index] ^ b[index];
  return difference === 0;
}

export function ingestAuthorized(request: Request): boolean {
  const expected = ingestKey();
  if (!expected) {
    // Unset, ingestion is open — fine on a laptop, never in production, where
    // the bundler folds this branch to `false` and the deployment fails
    // closed rather than accepting anonymous writes.
    return process.env.NODE_ENV !== 'production';
  }
  return sameSecret(request.headers.get('authorization') ?? '', `Bearer ${expected}`);
}

/**
 * Who is writing, and to which project.
 *
 * A project-scoped API key answers both questions at once, which is the point
 * of having them: one shared secret means a leak is unbounded and revoking it
 * takes every service down together. The legacy shared key still works —
 * upgrading should not break a running deployment — but it cannot say which
 * project it is for, so the payload's own `project` field decides, defaulting
 * to `default`.
 *
 * Returns the project id to write into, or `null` when the caller is not
 * authorised at all.
 */
export async function authorizeIngest(
  request: Request,
  fallbackProject = 'default',
): Promise<string | null> {
  const presented = apiKeyFromRequest(request);
  if (presented) {
    const projectId = await projectForApiKey(presented);
    if (projectId) return projectId;
  }
  // Not a known API key. Fall back to the shared secret, if one is set.
  if (ingestAuthorized(request)) {
    return resolveProject(fallbackProject);
  }
  return null;
}

export const MAX_BODY_BYTES = 2_000_000;

/**
 * Read a JSON body, refusing anything oversized.
 *
 * The old check trusted `content-length`, which a chunked request simply
 * omits — so the guard read 0, passed, and the whole body was buffered
 * anyway. Measuring the bytes we actually received is the only version that
 * holds. Malformed JSON returns 400 rather than throwing into a 500.
 */
export async function readJsonBody(
  request: Request,
): Promise<{ ok: true; value: unknown } | { ok: false; response: Response }> {
  if (Number(request.headers.get('content-length') ?? 0) > MAX_BODY_BYTES) {
    return { ok: false, response: Response.json({ error: 'body too large' }, { status: 413 }) };
  }
  const raw = await request.arrayBuffer();
  if (raw.byteLength > MAX_BODY_BYTES) {
    return { ok: false, response: Response.json({ error: 'body too large' }, { status: 413 }) };
  }
  try {
    return { ok: true, value: JSON.parse(new TextDecoder().decode(raw)) };
  } catch {
    return { ok: false, response: Response.json({ error: 'invalid JSON' }, { status: 400 }) };
  }
}

/** Kept for callers that only need the size guard. */
export function bodyTooLarge(request: Request): boolean {
  return Number(request.headers.get('content-length') ?? 0) > MAX_BODY_BYTES;
}
