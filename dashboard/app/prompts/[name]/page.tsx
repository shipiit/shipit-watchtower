import { ArrowLeft, BookOpen, GitCommitHorizontal } from 'lucide-react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { relative } from '@/components/format';
import { listPromptVersions } from '@/lib/repository';

export const dynamic = 'force-dynamic';

interface Version { id: string; name: string; version: number; prompt_type: string; prompt: unknown; labels: string[]; tags: string[]; config: Record<string, unknown>; commit_message: string | null; created_at: number }

export default async function PromptDetailPage({ params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  const versions = await listPromptVersions(decodeURIComponent(name)) as unknown as Version[];
  if (!versions.length) notFound();
  const latest = versions[0];
  return <><div className="detail-back"><Link href="/prompts"><ArrowLeft size={14}/>All prompts</Link><span>Published {relative(latest.created_at)}</span></div><header className="detail-header"><div><span className="trace-kicker"><BookOpen size={14}/>PROMPT</span><h1>{latest.name}</h1><code>{versions.length} immutable version{versions.length === 1 ? '' : 's'}</code></div></header><section className="detail-grid prompt-detail"><article className="panel"><div className="panel-title"><div><h2>Latest template · v{latest.version}</h2><p>{latest.labels.join(', ') || 'No deployment label'}</p></div></div><pre>{typeof latest.prompt === 'string' ? latest.prompt : JSON.stringify(latest.prompt, null, 2)}</pre></article><aside className="detail-aside"><article className="panel"><div className="panel-title"><h2>Configuration</h2></div><pre>{JSON.stringify(latest.config, null, 2)}</pre></article></aside></section><section className="panel version-list"><div className="panel-title"><h2>Version history</h2></div>{versions.map((version) => <div key={version.id}><GitCommitHorizontal size={15}/><span><strong>Version {version.version}</strong><small>{version.commit_message || 'No commit message'} · {relative(version.created_at)}</small></span><em>{version.labels.join(', ') || 'staged'}</em></div>)}</section></>;
}
