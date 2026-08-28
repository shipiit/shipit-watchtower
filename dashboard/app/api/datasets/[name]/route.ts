import { getDatasetRecord } from '@/lib/repository';

export const dynamic = 'force-dynamic';

export async function GET(_: Request, { params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  const dataset = await getDatasetRecord(decodeURIComponent(name));
  return dataset ? Response.json(dataset) : Response.json({ error: 'dataset not found' }, { status: 404 });
}
