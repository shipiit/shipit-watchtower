import { ingestAuthorized, readJsonBody } from '@/lib/ingest-auth';
import { createPrompt, listPrompts } from '@/lib/repository';

export const dynamic = 'force-dynamic';

export async function GET() {
  return Response.json({ prompts: await listPrompts() });
}

export async function POST(request: Request) {
  if (!ingestAuthorized(request)) return Response.json({ error: 'unauthorized' }, { status: 401 });
  const body = await readJsonBody(request);
  if (!body.ok) return body.response;
  const payload = body.value as Record<string, unknown>;
  if (typeof payload.name !== 'string' || !payload.name || payload.prompt == null) return Response.json({ error: 'name and prompt are required' }, { status: 422 });
  return Response.json(await createPrompt(payload), { status: 201 });
}
