import { overview } from '@/lib/repository';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const days = Number(new URL(request.url).searchParams.get('days') ?? 7);
  return Response.json(await overview(Number.isFinite(days) ? days : 7), {
    headers: { 'Cache-Control': 'private, max-age=5' },
  });
}
