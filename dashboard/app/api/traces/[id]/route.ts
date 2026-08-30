import { getTrace } from '@/lib/repository';

export const dynamic = 'force-dynamic';

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const trace = await getTrace(id);
  if (!trace) return Response.json({ error: 'trace not found' }, { status: 404 });
  return Response.json(trace);
}
