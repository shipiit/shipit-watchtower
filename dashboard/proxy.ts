import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { SESSION_COOKIE, passwordConfigured, sessionValid } from '@/lib/auth';

/**
 * One gate in front of everything, rather than a check per route.
 *
 * Writes were guarded from the start; reads were not — so every trace body,
 * prompt and cost figure was readable by anyone who knew the URL. Reads are
 * the more sensitive half: a trace contains whatever the prompt and the
 * completion contained.
 *
 * Ingest endpoints are exempt here because they authenticate differently,
 * with a project-scoped API key rather than a browser session. They are not
 * unauthenticated — see `lib/ingest-auth.ts`.
 */

const INGEST_PATHS = [
  '/api/ingest', '/api/scores', '/api/datasets', '/api/dataset-items',
  '/api/experiments', '/api/prompts',
];

const PUBLIC_PATHS = ['/login', '/api/auth/login'];

/** Assets are served as-is; gating them only breaks the sign-in page's own CSS. */
const ASSET = /\.(?:svg|png|jpe?g|gif|webp|ico|css|js|map|woff2?|txt|xml)$/i;

export default async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (pathname.startsWith('/_next') || ASSET.test(pathname)) return NextResponse.next();
  if (PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`))) {
    return NextResponse.next();
  }

  // Machine writes carry their own credential and are checked by the route.
  // Reads of those same paths still need a session, so the method matters.
  if (request.method === 'POST' && INGEST_PATHS.some((path) => pathname.startsWith(path))) {
    return NextResponse.next();
  }

  if (await sessionValid(request.cookies.get(SESSION_COOKIE)?.value)) {
    return NextResponse.next();
  }

  if (!passwordConfigured()) {
    // Nothing to check against. On a laptop that is the point; in production
    // it is an open dashboard, and serving one silently is worse than
    // refusing — a 503 gets fixed, an open dashboard does not get noticed.
    if (process.env.NODE_ENV === 'production') {
      return Response.json(
        {
          error: 'dashboard is unprotected',
          fix: 'Set WATCHER_DASHBOARD_PASSWORD (npx wrangler secret put WATCHER_DASHBOARD_PASSWORD), then redeploy.',
        },
        { status: 503 },
      );
    }
    return NextResponse.next();
  }

  if (pathname.startsWith('/api/')) {
    return Response.json({ error: 'unauthorized' }, { status: 401 });
  }

  const login = request.nextUrl.clone();
  login.pathname = '/login';
  // Carried through so signing in returns you to the page you asked for,
  // rather than dumping you on the overview.
  login.search = `?next=${encodeURIComponent(pathname + request.nextUrl.search)}`;
  return NextResponse.redirect(login);
}
