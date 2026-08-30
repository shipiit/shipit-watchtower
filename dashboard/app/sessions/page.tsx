import { MessageSquareText } from 'lucide-react';
import Link from 'next/link';
import { duration, integer, money, relative } from '@/components/format';
import { EmptyState } from '@/components/empty-state';
import { PageHeader } from '@/components/page-header';
import { listSessions } from '@/lib/repository';

export const dynamic = 'force-dynamic';

interface Session { id: string; user_id: string | null; traces: number; cost: number; tokens: number; latency: number; quality: number | null; started_at: number; ended_at: number }

export default async function SessionsPage() {
  const sessions = await listSessions() as unknown as Session[];
  return <><PageHeader eyebrow="Observe" title="Sessions" description="Replay multi-turn conversations and distributed agent workflows."/>{sessions.length ? <section className="panel entity-table"><div className="entity-row heading"><span>Session</span><span>User</span><span>Traces</span><span>Tokens</span><span>Avg. latency</span><span>Cost</span><span>Last active</span></div>{sessions.map((session) => <Link href={`/sessions/${encodeURIComponent(session.id)}`} className="entity-row" key={session.id}><span><i className="entity-icon"><MessageSquareText size={15}/></i><strong>{session.id}</strong></span><span>{session.user_id || 'Anonymous'}</span><span>{integer(session.traces)}</span><span>{integer(session.tokens)}</span><span>{duration(session.latency)}</span><span>{money(session.cost)}</span><span>{relative(session.ended_at)}</span></Link>)}</section> : <EmptyState title="No sessions yet"/>}</>;
}
