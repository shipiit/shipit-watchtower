import { ShieldCheck } from 'lucide-react';
import Link from 'next/link';
import { relative } from '@/components/format';
import { EmptyState } from '@/components/empty-state';
import { PageHeader } from '@/components/page-header';
import { decode } from '@/lib/db';
import { listPolicies } from '@/lib/repository';

export const dynamic = 'force-dynamic';

interface Policy { id: string; trace_id: string; name: string; severity: string; status_message: string | null; metadata_json: string; user_id: string | null; session_id: string | null; started_at: number }

export default async function PoliciesPage() {
  const policies = await listPolicies() as unknown as Policy[];
  return <><PageHeader eyebrow="Govern" title="Policies" description="Audit every persisted prompt, model, tool, privacy, and budget decision."/>{policies.length ? <section className="panel entity-table policy-table"><div className="entity-row heading"><span>Policy event</span><span>Decision</span><span>User</span><span>Session</span><span>Reason</span><span>Time</span></div>{policies.map((policy) => { const metadata = decode<Record<string, unknown>>(policy.metadata_json, {}); const blocked = Boolean(metadata.blocked); return <Link href={`/traces/${policy.trace_id}`} className="entity-row" key={policy.id}><span><i className="entity-icon"><ShieldCheck size={15}/></i><strong>{policy.name.replace('policy.', '')}</strong></span><span className={blocked ? 'decision-block' : 'decision-allow'}>{blocked ? 'Blocked' : 'Allowed'}</span><span>{policy.user_id || '—'}</span><span>{policy.session_id || '—'}</span><span>{String(metadata.reason || policy.status_message || '—')}</span><span>{relative(policy.started_at)}</span></Link>; })}</section> : <EmptyState title="No policy decisions recorded"/>}</>;
}
