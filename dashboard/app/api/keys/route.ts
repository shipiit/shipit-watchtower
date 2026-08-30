import { createApiKey, listApiKeys, revokeApiKey } from '@/lib/repository';
import { readJsonBody } from '@/lib/ingest-auth';

export const dynamic = 'force-dynamic';

// Every handler here is session-gated by `proxy.ts` — key management is a
// human action, and an API key that can mint API keys is a privilege
// escalation waiting to be found.

export async function GET(request: Request) {
  const project = new URL(request.url).searchParams.get('project') ?? 'default';
  return Response.json({ keys: await listApiKeys(project) });
}

export async function POST(request: Request) {
  const body = await readJsonBody(request);
  if (!body.ok) return body.response;
  const payload = body.value as { name?: unknown; project?: unknown };
  const name = typeof payload.name === 'string' ? payload.name.trim() : '';
  if (!name) return Response.json({ error: 'name is required' }, { status: 422 });
  const project = typeof payload.project === 'string' ? payload.project : 'default';
  // The plaintext key is in this response and nowhere else, ever again.
  return Response.json(await createApiKey(project, name), { status: 201 });
}

export async function DELETE(request: Request) {
  const id = new URL(request.url).searchParams.get('id');
  if (!id) return Response.json({ error: 'id is required' }, { status: 422 });
  await revokeApiKey(id);
  return Response.json({ revoked: true });
}
