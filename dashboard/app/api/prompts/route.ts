import { authorizeIngest, readJsonBody } from '@/lib/ingest-auth';
import { createPrompt, listPrompts } from '@/lib/repository';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const projectId = await authorizeIngest(
    request, new URL(request.url).searchParams.get('project') ?? 'default',
  );
  if (!projectId) return Response.json({ error: 'unauthorized' }, { status: 401 });
  return Response.json({ prompts: await listPrompts(projectId) });
}

export async function POST(request: Request) {
  const body = await readJsonBody(request);
  if (!body.ok) return body.response;
  const payload = body.value as Record<string, unknown>;
  const projectId = await authorizeIngest(
    request, typeof payload.project === 'string' ? payload.project : 'default',
  );
  if (!projectId) return Response.json({ error: 'unauthorized' }, { status: 401 });
  if (typeof payload.name !== 'string' || !payload.name || payload.prompt == null) return Response.json({ error: 'name and prompt are required' }, { status: 422 });
  return Response.json(await createPrompt(payload, projectId), { status: 201 });
}
