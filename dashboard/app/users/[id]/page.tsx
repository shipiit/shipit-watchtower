import { ArrowLeft, Clock3, Coins, Hash, MessageSquareText, UserRound } from 'lucide-react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { duration, integer, money, relative } from '@/components/format';
import { EntityAnalytics } from '@/components/entity-analytics';
import { TraceTable } from '@/components/trace-table';
import { getUser } from '@/lib/repository';

export const dynamic = 'force-dynamic';

interface Summary { id: string; company_id: string | null; traces: number; sessions: number; cost: number; tokens: number; latency: number; quality: number | null; first_seen: number; last_seen: number }
interface Session { id: string; traces: number; cost: number; tokens: number; ended_at: number }

export default async function UserDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const result = await getUser(decodeURIComponent(id));
  if (!result) notFound();
  const user = result.summary as unknown as Summary;
  const sessions = result.sessions as unknown as Session[];
  return <>
    <div className="detail-back"><Link href="/users"><ArrowLeft size={14}/>All users</Link><span>Seen {relative(user.last_seen)}</span></div>
    <header className="detail-header"><div><span className="trace-kicker"><UserRound size={14}/>USER</span><h1>{user.id}</h1><code>{user.company_id || 'No company attached'}</code></div></header>
    <section className="detail-stats"><div><Hash size={15}/><span>Traces<strong>{integer(user.traces)}</strong></span></div><div><MessageSquareText size={15}/><span>Sessions<strong>{integer(user.sessions)}</strong></span></div><div><Coins size={15}/><span>Cost<strong>{money(user.cost)}</strong></span></div><div><Clock3 size={15}/><span>Avg. latency<strong>{duration(user.latency)}</strong></span></div><div><Hash size={15}/><span>Tokens<strong>{integer(user.tokens)}</strong></span></div></section>
    <EntityAnalytics timeline={result.timeline} models={result.models} scores={result.scores}/>
    {sessions.length > 0 && <section className="panel entity-table"><div className="entity-row heading"><span>Session</span><span>Traces</span><span>Tokens</span><span>Cost</span><span>Last active</span><span/><span/></div>{sessions.map((session) => <Link className="entity-row" href={`/sessions/${encodeURIComponent(session.id)}`} key={session.id}><span><i className="entity-icon"><MessageSquareText size={15}/></i><strong>{session.id}</strong></span><span>{integer(session.traces)}</span><span>{integer(session.tokens)}</span><span>{money(session.cost)}</span><span>{relative(session.ended_at)}</span><span/><span/></Link>)}</section>}
    <section className="panel explorer-panel entity-traces"><TraceTable traces={result.traces}/></section>
  </>;
}
