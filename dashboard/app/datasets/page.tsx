import { Database } from 'lucide-react';
import Link from 'next/link';
import { EmptyState } from '@/components/empty-state';
import { relative } from '@/components/format';
import { PageHeader } from '@/components/page-header';
import { listDatasets } from '@/lib/repository';

export const dynamic = 'force-dynamic';

interface Dataset { id: string; name: string; description: string | null; items: number; runs: number; created_at: number; last_item_at: number | null }

export default async function DatasetsPage() {
  const datasets = await listDatasets() as unknown as Dataset[];
  return <><PageHeader eyebrow="Improve" title="Datasets & experiments" description="Capture production failures, replay fixed inputs, and compare quality, cost, and trajectory."/>{datasets.length ? <section className="panel entity-table"><div className="entity-row heading"><span>Dataset</span><span>Items</span><span>Runs</span><span>Description</span><span>Last capture</span><span>Created</span><span/></div>{datasets.map((dataset) => <Link className="entity-row" href={`/datasets/${encodeURIComponent(dataset.name)}`} key={dataset.id}><span><i className="entity-icon"><Database size={15}/></i><strong>{dataset.name}</strong></span><span>{dataset.items}</span><span>{dataset.runs}</span><span>{dataset.description || '—'}</span><span>{dataset.last_item_at ? relative(dataset.last_item_at) : '—'}</span><span>{relative(dataset.created_at)}</span><span/></Link>)}</section> : <EmptyState title="No datasets captured yet"/>}</>;
}
