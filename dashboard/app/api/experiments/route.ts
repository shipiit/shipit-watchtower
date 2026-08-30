import { authorizeIngest, readJsonBody } from '@/lib/ingest-auth';
import { linkExperiment } from '@/lib/repository';

export async function POST(request: Request) {
  const body = await readJsonBody(request);
  if (!body.ok) return body.response;
  const payload = body.value as Record<string, unknown>;
  const projectId = await authorizeIngest(
    request, typeof payload.project === 'string' ? payload.project : 'default',
  );
  if (!projectId) return Response.json({ error: 'unauthorized' }, { status: 401 });
  if (typeof payload.dataset_item_id !== 'string' || typeof payload.run_name !== 'string') return Response.json({ error: 'dataset_item_id and run_name are required' }, { status: 422 });
  try {
    return Response.json(await linkExperiment(payload, projectId), { status: 201 });
  } catch (error) {
    const missing = String(error).includes('dataset item not found');
    return Response.json(
      { error: missing ? 'dataset item not found' : 'could not link experiment' },
      { status: missing ? 404 : 500 },
    );
  }
}
