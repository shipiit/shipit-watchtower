import { CheckCircle2, Terminal } from 'lucide-react';
import { ApiKeys, type ApiKeyRow } from '@/components/api-keys';
import { PageHeader } from '@/components/page-header';
import { listApiKeys } from '@/lib/repository';

export const dynamic = 'force-dynamic';

export default async function SettingsPage() {
  const keys = await listApiKeys('default') as unknown as ApiKeyRow[];
  return <><PageHeader eyebrow="Workspace" title="Settings & setup" description="A secure production checklist for the Watcher data plane."/><section className="settings-grid"><article className="panel"><div className="panel-title"><div><h2>Install the SDK</h2><p>Core is dependency-free; add only the integrations you use.</p></div><Terminal size={17}/></div><pre>pip install &quot;shipit-watcher[all]&quot;</pre></article><article className="panel"><div className="panel-title"><div><h2>Configure ingestion</h2><p>Keep the token in your secret manager.</p></div><CheckCircle2 size={17}/></div><pre>{`WATCHER_DASHBOARD_URL=https://your-watcher.example\nWATCHER_DASHBOARD_TOKEN=your-secret-token\nWATCHER_CONTENT_POLICY=redacted`}</pre></article><article className="panel"><div className="panel-title"><div><h2>Verify delivery</h2><p>Run a real diagnostic trace from your deployment.</p></div><Terminal size={17}/></div><pre>{`watcher doctor\nwatcher test-trace`}</pre></article><article className="panel"><div className="panel-title"><div><h2>Application startup</h2><p>One call detects backends and owns instrumentation.</p></div><CheckCircle2 size={17}/></div><pre>{`import shipit_watcher as wt\nwt.setup(service_name="my-app", environment="production")`}</pre></article></section><ApiKeys initialKeys={keys}/></>;
}
