import { SESSION_COOKIE, issueSession, passwordAccepted, passwordConfigured } from '@/lib/auth';

export const dynamic = 'force-dynamic';

export async function POST(request: Request) {
  if (!passwordConfigured()) {
    return Response.json(
      { error: 'no dashboard password is configured' },
      { status: 503 },
    );
  }

  let password = '';
  const contentType = request.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) {
    const body = (await request.json().catch(() => ({}))) as { password?: unknown };
    password = typeof body.password === 'string' ? body.password : '';
  } else {
    password = String((await request.formData()).get('password') ?? '');
  }

  if (!(await passwordAccepted(password))) {
    // Deliberately not "wrong password for that user" or a timing hint: there
    // is one secret here, and the only useful signal is whether it matched.
    return Response.json({ error: 'incorrect password' }, { status: 401 });
  }

  const session = await issueSession();
  const response = Response.json({ ok: true });
  response.headers.append(
    'set-cookie',
    `${SESSION_COOKIE}=${session.value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${session.maxAge}` +
      (process.env.NODE_ENV === 'production' ? '; Secure' : ''),
  );
  return response;
}
