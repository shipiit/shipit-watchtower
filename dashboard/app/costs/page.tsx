import { integer, money } from '@/components/format';
import { EmptyState } from '@/components/empty-state';
import { PageHeader } from '@/components/page-header';
import { costBreakdown } from '@/lib/repository';

export const dynamic = 'force-dynamic';

interface CostRow { label?: string | null; model?: string | null; provider?: string | null; traces?: number; calls?: number; cost: number; tokens: number }

function CostList({ title, description, rows }: { title: string; description: string; rows: CostRow[] }) {
  const maximum = Math.max(...rows.map((row) => Number(row.cost)), 0.000001);
  return <article className="panel cost-panel"><div className="panel-title"><div><h2>{title}</h2><p>{description}</p></div></div>{rows.length ? <div className="cost-list">{rows.map((row, index) => <div key={`${row.label || row.model}/${index}`}><span><strong>{row.label || row.model || 'Unknown'}</strong><small>{row.provider || `${integer(Number(row.traces || row.calls || 0))} operations`}</small></span><i><em style={{ width: `${Number(row.cost) / maximum * 100}%` }}/></i><span><strong>{money(Number(row.cost))}</strong><small>{integer(Number(row.tokens))} tokens</small></span></div>)}</div> : <div className="panel-empty">No cost data for this dimension.</div>}</article>;
}

export default async function CostsPage() {
  const data = await costBreakdown() as unknown as { models: CostRow[]; users: CostRow[]; costCenters: CostRow[] };
  const hasData = data.models.length || data.users.length || data.costCenters.length;
  return <><PageHeader eyebrow="Govern" title="Costs & budgets" description="Allocate persisted spend by model, user, tenant, agent, prompt, and cost centre."/>{hasData ? <section className="cost-grid"><CostList title="Cost by model" description="Generation spend and tokens" rows={data.models}/><CostList title="Cost by user" description="End-user consumption" rows={data.users}/><CostList title="Cost by cost centre" description="Business allocation" rows={data.costCenters}/></section> : <EmptyState title="No cost data yet"/>}</>;
}
