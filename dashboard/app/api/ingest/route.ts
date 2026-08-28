import { ingestTrace } from '@/lib/repository';
import { authorizeIngest, readJsonBody } from '@/lib/ingest-auth';
import type { IngestTrace } from '@/lib/types';

export const runtime = 'edge';

function valid(payload: unknown): payload is IngestTrace {
  if (!payload || typeof payload !== 'object') return false;
  const trace = payload as Partial<IngestTrace>;
  return trace.schema_version === 'watcher.trace.v1'
    && typeof trace.trace_id === 'string'
    && /^[a-f0-9]{32}$/i.test(trace.trace_id)
    && typeof trace.name === 'string'
    && Array.isArray(trace.events);
}

export async function POST(request: Request) {
  const body = await readJsonBody(request);
  if (!body.ok) return body.response;
  const payload = body.value;
  if (!valid(payload)) {
    return Response.json({ error: 'invalid watcher.trace.v1 bundle' }, { status: 422 });
  }
  // Authorised after parsing, because a shared ingest key cannot say which
  // project it is for — the bundle's own `project` field decides. An API key
  // is scoped to a project and overrides whatever the payload claims.
  const projectId = await authorizeIngest(request, payload.project ?? 'default');
  if (!projectId) return Response.json({ error: 'unauthorized' }, { status: 401 });
  await ingestTrace(payload, projectId);
  return Response.json({ accepted: true, trace_id: payload.trace_id }, { status: 202 });
}
