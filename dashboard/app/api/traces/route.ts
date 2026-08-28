import { listTraces } from '@/lib/repository';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const url = new URL(request.url);
  const limit = Number(url.searchParams.get('limit') ?? 50);
  const numeric = (key: string) => url.searchParams.has(key) ? Number(url.searchParams.get(key)) : undefined;
  return Response.json({ traces: await listTraces({
    limit,
    search: url.searchParams.get('search') ?? undefined,
    status: url.searchParams.get('status') ?? undefined,
    user: url.searchParams.get('user') ?? undefined,
    session: url.searchParams.get('session') ?? undefined,
    model: url.searchParams.get('model') ?? undefined,
    provider: url.searchParams.get('provider') ?? undefined,
    name: url.searchParams.get('name') ?? undefined,
    traceId: url.searchParams.get('traceId') ?? undefined,
    company: url.searchParams.get('company') ?? undefined,
    costCenter: url.searchParams.get('costCenter') ?? undefined,
    channel: url.searchParams.get('channel') ?? undefined,
    metadata: url.searchParams.get('metadata') ?? undefined,
    eventType: url.searchParams.get('eventType') ?? undefined,
    minLatency: numeric('minLatency'), maxLatency: numeric('maxLatency'),
    minTokens: numeric('minTokens'), maxTokens: numeric('maxTokens'),
    minCost: numeric('minCost'), maxCost: numeric('maxCost'),
    minObservations: numeric('minObservations'), maxObservations: numeric('maxObservations'),
    minQuality: numeric('minQuality'),
  }) });
}
