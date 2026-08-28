import { env } from 'cloudflare:workers';

/**
 * Two different things are being authenticated here, and conflating them is
 * how observability dashboards end up world-readable:
 *
 * **Machines write.** The SDK presents an API key. A key is scoped to one
 * project, so what a compromised key can write is bounded, and it can be
 * revoked without touching anything else.
 *
 * **People read.** A browser presents a signed session cookie, obtained by
 * entering the dashboard password once. Trace bodies contain whatever the
 * prompts and completions contained, so a read is at least as sensitive as a
 * write — arguably more.
 */

type WorkerEnv = {
  WATCHER_INGEST_KEY?: string;
  WATCHER_DASHBOARD_PASSWORD?: string;
  WATCHER_SESSION_SECRET?: string;
};

function secret(name: keyof WorkerEnv): string | undefined {
  // `lib/db.ts` reads its binding from `cloudflare:workers`; secrets come from
  // the same place. Reading one from `process.env` and the other from the
  // Worker env is how a deployment ends up failing closed for reasons nobody
  // can see. `process.env` stays as a fallback for local tooling.
  const fromWorker = (env as unknown as WorkerEnv)?.[name];
  return fromWorker || process.env[name] || undefined;
}

export const SESSION_COOKIE = 'watcher_session';
const SESSION_TTL_SECONDS = 60 * 60 * 12;

/** Constant-time compare, so a secret cannot be probed byte by byte. */
function sameBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.byteLength !== b.byteLength) return false;
  const native = (crypto.subtle as { timingSafeEqual?(x: ArrayBufferView, y: ArrayBufferView): boolean })
    .timingSafeEqual;
  if (native) return native.call(crypto.subtle, a, b);
  let difference = 0;
  for (let index = 0; index < a.byteLength; index += 1) difference |= a[index] ^ b[index];
  return difference === 0;
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** The stored form of an API key. The plaintext is never persisted. */
export const hashApiKey = (key: string) => sha256(key);

export function apiKeyFromRequest(request: Request): string | null {
  const header = request.headers.get('authorization') ?? '';
  return header.startsWith('Bearer ') ? header.slice(7).trim() || null : null;
}

// ── people ──────────────────────────────────────────────────────────────────

/**
 * Whether a password is configured at all.
 *
 * Unset, the dashboard is open. That is right for a laptop and wrong for
 * anything reachable, so `middleware.ts` refuses to serve an unprotected
 * dashboard in production rather than quietly serving one.
 */
export const passwordConfigured = () => Boolean(secret('WATCHER_DASHBOARD_PASSWORD'));

function sessionKeyMaterial(): string {
  // Derived from the password when no separate secret is set, so there is one
  // less thing to configure — and changing the password invalidates every
  // existing session, which is the behaviour people expect from a password
  // change anyway.
  return secret('WATCHER_SESSION_SECRET') || `session:${secret('WATCHER_DASHBOARD_PASSWORD') ?? ''}`;
}

async function signingKey(): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(sessionKeyMaterial()),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign', 'verify'],
  );
}

export async function passwordAccepted(candidate: string): Promise<boolean> {
  const expected = secret('WATCHER_DASHBOARD_PASSWORD');
  if (!expected) return false;
  const encoder = new TextEncoder();
  return sameBytes(encoder.encode(candidate), encoder.encode(expected));
}

/** A cookie value of `<expires-at>.<hmac>`. Stateless: no session table. */
export async function issueSession(): Promise<{ value: string; maxAge: number }> {
  const expiresAt = Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS;
  const signature = await crypto.subtle.sign(
    'HMAC',
    await signingKey(),
    new TextEncoder().encode(String(expiresAt)),
  );
  const encoded = [...new Uint8Array(signature)]
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
  return { value: `${expiresAt}.${encoded}`, maxAge: SESSION_TTL_SECONDS };
}

export async function sessionValid(cookie: string | undefined): Promise<boolean> {
  if (!cookie) return false;
  const [expiresAt, signature] = cookie.split('.');
  if (!expiresAt || !signature) return false;
  if (Number(expiresAt) < Math.floor(Date.now() / 1000)) return false;
  const bytes = signature.match(/../g)?.map((pair) => Number.parseInt(pair, 16));
  if (!bytes || bytes.length !== 32) return false;
  return crypto.subtle.verify(
    'HMAC',
    await signingKey(),
    new Uint8Array(bytes),
    new TextEncoder().encode(expiresAt),
  );
}

export function readCookie(request: Request, name: string): string | undefined {
  const header = request.headers.get('cookie');
  if (!header) return undefined;
  for (const part of header.split(';')) {
    const [key, ...rest] = part.trim().split('=');
    if (key === name) return rest.join('=');
  }
  return undefined;
}

/** The legacy shared ingest key, still honoured so upgrades do not break. */
export const legacyIngestKey = () => secret('WATCHER_INGEST_KEY');
