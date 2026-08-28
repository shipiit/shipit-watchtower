import { ArrowLeft, Clock3, Coins, Hash, MessageSquareText, UserRound } from 'lucide-react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { duration, integer, money, relative } from '@/components/format';
import { EntityAnalytics } from '@/components/entity-analytics';
import { SessionReplay } from '@/components/session-replay';
import { TraceTable } from '@/components/trace-table';
import { getSession } from '@/lib/repository';

export const dynamic = 'force-dynamic';

interface Summary { id: string; user_id: string | null; company_id: string | null; traces: number; cost: number; tokens: number; latency: number; quality: number | null; started_at: number; ended_at: number }

export default async function SessionDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const result = await getSession(decodeURIComponent(id));
  if (!result) notFound();
  const session = result.summary as unknown as Summary;
  return <>
    <div className="detail-back"><Link href="/sessions"><ArrowLeft size={14}/>All sessions</Link><span>Active {relative(session.ended_at)}</span></div>
    <header className="detail-header"><div><span className="trace-kicker"><MessageSquareText size={14}/>SESSION</span><h1>{session.id}</h1><code>{session.company_id || 'No company attached'}</code></div></header>
    <section className="detail-stats"><div><Hash size={15}/><span>Traces<strong>{integer(session.traces)}</strong></span></div><div><UserRound size={15}/><span>User<strong>{session.user_id || 'Anonymous'}</strong></span></div><div><Coins size={15}/><span>Cost<strong>{money(session.cost)}</strong></span></div><div><Clock3 size={15}/><span>Avg. latency<strong>{duration(session.latency)}</strong></span></div><div><Hash size={15}/><span>Tokens<strong>{integer(session.tokens)}</strong></span></div></section>
    <EntityAnalytics timeline={result.timeline} models={result.models} types={result.types}/>
    <SessionReplay traces={result.traces}/>
    <section className="panel explorer-panel"><TraceTable traces={result.traces}/></section>
  </>;
}
