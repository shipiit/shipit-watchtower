import { ArrowLeft, Database, ExternalLink } from 'lucide-react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { relative } from '@/components/format';
import { getDatasetRecord } from '@/lib/repository';

export const dynamic = 'force-dynamic';

interface Item { id: string; input: unknown; expected_output: unknown; source_trace_id: string | null; experiment_runs: number; created_at: number }
interface Dataset { id: string; name: string; description: string | null; items: Item[] }

export default async function DatasetDetailPage({ params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  const record = await getDatasetRecord(decodeURIComponent(name));
  if (!record) notFound();
  const dataset = record as unknown as Dataset;
  return <><div className="detail-back"><Link href="/datasets"><ArrowLeft size={14}/>All datasets</Link><span>{dataset.items.length} items</span></div><header className="detail-header"><div><span className="trace-kicker"><Database size={14}/>DATASET</span><h1>{dataset.name}</h1><code>{dataset.description || 'No description'}</code></div></header><section className="panel dataset-items"><div className="panel-title"><h2>Captured examples</h2></div>{dataset.items.length ? dataset.items.map((item) => <article key={item.id}><header><code>{item.id}</code><span>{item.experiment_runs} runs · {relative(item.created_at)}</span>{item.source_trace_id ? <Link href={`/traces/${item.source_trace_id}`}>Origin trace <ExternalLink size={12}/></Link> : null}</header><div><pre>{JSON.stringify(item.input, null, 2)}</pre><pre>{JSON.stringify(item.expected_output, null, 2)}</pre></div></article>) : <p className="table-empty">No captured examples yet.</p>}</section></>;
}
