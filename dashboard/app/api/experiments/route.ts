import { ingestAuthorized, readJsonBody } from '@/lib/ingest-auth';
import { linkExperiment } from '@/lib/repository';

export async function POST(request: Request) {
  if (!ingestAuthorized(request)) return Response.json({ error: 'unauthorized' }, { status: 401 });
  const body = await readJsonBody(request);
  if (!body.ok) return body.response;
  const payload = body.value as Record<string, unknown>;
  if (typeof payload.dataset_item_id !== 'string' || typeof payload.run_name !== 'string') return Response.json({ error: 'dataset_item_id and run_name are required' }, { status: 422 });
  return Response.json(await linkExperiment(payload), { status: 201 });
}
