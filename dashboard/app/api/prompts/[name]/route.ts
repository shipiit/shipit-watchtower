import { getPromptVersion } from '@/lib/repository';

export const dynamic = 'force-dynamic';

export async function GET(request: Request, { params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  const query = new URL(request.url).searchParams;
  const prompt = await getPromptVersion(decodeURIComponent(name), query.get('version') ?? undefined, query.get('label') ?? undefined);
  return prompt ? Response.json(prompt) : Response.json({ error: 'prompt not found' }, { status: 404 });
}
