import { BookOpen } from 'lucide-react';
import Link from 'next/link';
import { EmptyState } from '@/components/empty-state';
import { relative } from '@/components/format';
import { PageHeader } from '@/components/page-header';
import { listPrompts } from '@/lib/repository';

export const dynamic = 'force-dynamic';

interface Prompt { id: string; name: string; version: number; versions: number; prompt_type: string; labels: string[]; config: Record<string, unknown>; created_at: number }

export default async function PromptsPage() {
  const prompts = await listPrompts() as unknown as Prompt[];
  return <><PageHeader eyebrow="Improve" title="Prompts" description="Version, approve, promote, compare, and govern every production prompt."/>{prompts.length ? <section className="panel entity-table"><div className="entity-row heading"><span>Prompt</span><span>Latest version</span><span>Versions</span><span>Type</span><span>Labels</span><span>Model</span><span>Published</span></div>{prompts.map((prompt) => <Link className="entity-row" href={`/prompts/${encodeURIComponent(prompt.name)}`} key={prompt.id}><span><i className="entity-icon"><BookOpen size={15}/></i><strong>{prompt.name}</strong></span><span>v{prompt.version}</span><span>{prompt.versions}</span><span>{prompt.prompt_type}</span><span>{prompt.labels.join(', ') || 'Unlabelled'}</span><span>{String(prompt.config.model ?? '—')}</span><span>{relative(prompt.created_at)}</span></Link>)}</section> : <EmptyState title="No managed prompts yet"/>}</>;
}
