import { BrainCircuit } from 'lucide-react';
import { duration, integer, money, relative } from '@/components/format';
import { EmptyState } from '@/components/empty-state';
import { PageHeader } from '@/components/page-header';
import { listModels } from '@/lib/repository';

export const dynamic = 'force-dynamic';

interface Model { model: string | null; provider: string | null; calls: number; prompt_tokens: number; completion_tokens: number; total_tokens: number; cost: number; latency: number; errors: number; last_seen: number }

export default async function ModelsPage() {
  const models = await listModels() as unknown as Model[];
  return <><PageHeader eyebrow="Observe" title="Models" description="Compare live provider performance, token mix, errors, latency, and cost."/>{models.length ? <section className="model-cards">{models.map((model) => <article className="panel model-card" key={`${model.provider}/${model.model}`}><header><span><BrainCircuit size={18}/></span><div><h2>{model.model || 'Unknown model'}</h2><p>{model.provider || 'Unknown provider'}</p></div><i className={model.errors ? 'warning' : 'healthy'}>{model.errors ? `${model.errors} errors` : 'Healthy'}</i></header><dl><div><dt>Calls</dt><dd>{integer(model.calls)}</dd></div><div><dt>Input tokens</dt><dd>{integer(model.prompt_tokens)}</dd></div><div><dt>Output tokens</dt><dd>{integer(model.completion_tokens)}</dd></div><div><dt>Average latency</dt><dd>{duration(model.latency)}</dd></div><div><dt>Total cost</dt><dd>{money(model.cost)}</dd></div><div><dt>Last seen</dt><dd>{relative(model.last_seen)}</dd></div></dl></article>)}</section> : <EmptyState title="No model generations yet"/>}</>;
}
