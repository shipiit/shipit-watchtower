import { Boxes, CheckCircle2 } from 'lucide-react';
import { CopyButton } from '@/components/copy-button';
import { PageHeader } from '@/components/page-header';
import { dashboardHealth } from '@/lib/repository';

const backends = [
  { name: 'Watcher dashboard', variable: 'WATCHER_DASHBOARD_URL', detail: 'Canonical trace store and this control plane' },
  { name: 'Langfuse', variable: 'LANGFUSE_PUBLIC_KEY', detail: 'OTLP trace export, prompts, datasets, and scores' },
  { name: 'Phoenix', variable: 'PHOENIX_COLLECTOR_ENDPOINT', detail: 'OpenInference traces, annotations, prompts, and experiments' },
  { name: 'LangSmith', variable: 'LANGSMITH_API_KEY', detail: 'OTLP traces, feedback, prompts, datasets, and evaluation' },
];

export const dynamic = 'force-dynamic';

export default async function BackendsPage() {
  const health = await dashboardHealth();
  return <><PageHeader eyebrow="Govern" title="Backends" description="Instrument once and route clean telemetry to every AI observability system."/><section className="backend-grid">{backends.map((backend, index) => <article className="panel backend-card" key={backend.name}><header><span><Boxes size={17}/></span><div><h2>{backend.name}</h2><p>{backend.detail}</p></div>{index === 0 && Number(health?.traces) > 0 && <b><CheckCircle2 size={13}/>Receiving telemetry</b>}</header><footer><code>{backend.variable}</code><CopyButton value={backend.variable} label={`Copy ${backend.variable}`}/></footer></article>)}</section><article className="panel setup-panel"><div><h2>One-call setup</h2><p>Watcher auto-detects every configured destination and prevents duplicate LiteLLM instrumentation.</p></div><pre>{`import shipit_watcher as wt\n\nreport = wt.setup(\n    service_name="my-app",\n    environment="production",\n)\nassert report.ok, report.warnings\n\ngraph = wt.instrument_langgraph(graph)`}</pre></article></>;
}
