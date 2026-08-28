import { UserRound } from 'lucide-react';
import Link from 'next/link';
import { integer, money, relative } from '@/components/format';
import { EmptyState } from '@/components/empty-state';
import { PageHeader } from '@/components/page-header';
import { listUsers } from '@/lib/repository';

export const dynamic = 'force-dynamic';

interface User { id: string; company_id: string | null; traces: number; sessions: number; cost: number; tokens: number; quality: number | null; last_seen: number }

export default async function UsersPage() {
  const users = await listUsers() as unknown as User[];
  return <><PageHeader eyebrow="Observe" title="Users" description="Understand adoption, quality, token usage, and spend for every user."/>{users.length ? <section className="panel entity-table"><div className="entity-row heading"><span>User</span><span>Company</span><span>Sessions</span><span>Traces</span><span>Tokens</span><span>Cost</span><span>Last seen</span></div>{users.map((user) => <Link href={`/users/${encodeURIComponent(user.id)}`} className="entity-row" key={user.id}><span><i className="entity-icon"><UserRound size={15}/></i><strong>{user.id}</strong></span><span>{user.company_id || '—'}</span><span>{integer(user.sessions)}</span><span>{integer(user.traces)}</span><span>{integer(user.tokens)}</span><span>{money(user.cost)}</span><span>{relative(user.last_seen)}</span></Link>)}</section> : <EmptyState title="No identified users yet"/>}</>;
}
