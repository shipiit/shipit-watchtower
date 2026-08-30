import { listUsers } from '@/lib/repository';

export const dynamic = 'force-dynamic';

export async function GET() {
  return Response.json({ users: await listUsers() });
}
