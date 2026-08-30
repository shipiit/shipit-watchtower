import { getDatasetRecord } from '@/lib/repository';
import { authorizeIngest } from '@/lib/ingest-auth';

export const dynamic = 'force-dynamic';

export async function GET(request: Request, { params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  const query = new URL(request.url).searchParams;
  const projectId = await authorizeIngest(request, query.get('project') ?? 'default');
  if (!projectId) return Response.json({ error: 'unauthorized' }, { status: 401 });
  const dataset = await getDatasetRecord(decodeURIComponent(name), projectId);
  return dataset ? Response.json(dataset) : Response.json({ error: 'dataset not found' }, { status: 404 });
}
