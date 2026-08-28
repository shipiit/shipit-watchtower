import { ingestAuthorized, readJsonBody } from '@/lib/ingest-auth';
import { createDatasetItem } from '@/lib/repository';

export async function POST(request: Request) {
  if (!ingestAuthorized(request)) return Response.json({ error: 'unauthorized' }, { status: 401 });
  const body = await readJsonBody(request);
  if (!body.ok) return body.response;
  const payload = body.value as Record<string, unknown>;
  if (typeof payload.dataset_name !== 'string') return Response.json({ error: 'dataset_name is required' }, { status: 422 });
  try {
    return Response.json(await createDatasetItem(payload), { status: 201 });
  } catch (error) {
    // Only a missing dataset is the caller's fault. Reporting every failure
    // as 404 told an SDK to give up on what may have been a transient error.
    const missing = String(error).includes('dataset not found');
    return Response.json(
      { error: missing ? 'dataset not found' : 'could not store the item' },
      { status: missing ? 404 : 500 },
    );
  }
}
