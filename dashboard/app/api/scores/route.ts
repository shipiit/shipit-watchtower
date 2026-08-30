import { ingestScore } from '@/lib/repository';
import { authorizeIngest, readJsonBody } from '@/lib/ingest-auth';

export const runtime = 'edge';

export async function POST(request: Request) {
  const body = await readJsonBody(request);
  if (!body.ok) return body.response;
  const payload = body.value as Record<string, unknown>;
  if (typeof payload.id !== 'string' || typeof payload.trace_id !== 'string'
    || !/^[a-f0-9]{32}$/i.test(payload.trace_id)
    || typeof payload.name !== 'string') {
    return Response.json({ error: 'invalid score' }, { status: 422 });
  }
  const projectId = await authorizeIngest(request, String(payload.project ?? 'default'));
  if (!projectId) return Response.json({ error: 'unauthorized' }, { status: 401 });
  await ingestScore(payload, projectId);
  return Response.json({ accepted: true }, { status: 202 });
}
