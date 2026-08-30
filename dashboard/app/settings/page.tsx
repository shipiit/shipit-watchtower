import { CheckCircle2, KeyRound, PackageCheck, RadioTower } from 'lucide-react';
import { ApiKeys, type ApiKeyRow } from '@/components/api-keys';
import { PageHeader } from '@/components/page-header';
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle,
} from '@/components/ui/card';
import { listApiKeys } from '@/lib/repository';

export const dynamic = 'force-dynamic';

const STEPS = [
  {
    title: 'Install the SDK',
    description: 'Core stays dependency-light; the all extra enables every supported integration.',
    command: 'pip install "shipit-watcher[all]"',
    icon: PackageCheck,
  },
  {
    title: 'Connect this dashboard',
    description: 'Use a project key below and keep it in your deployment secret manager.',
    command: 'WATCHER_DASHBOARD_URL=http://localhost:3000\nWATCHER_DASHBOARD_TOKEN=wtk_…\nWATCHER_CONTENT_POLICY=redacted',
    icon: KeyRound,
  },
  {
    title: 'Start instrumentation',
    description: 'One call discovers configured backends and owns instrumentation lifecycle.',
    command: 'import shipit_watcher as wt\n\nwt.setup(service_name="my-app", environment="production")',
    icon: RadioTower,
  },
  {
    title: 'Verify real delivery',
    description: 'Doctor validates configuration; test-trace proves the complete write path.',
    command: 'watcher doctor\nwatcher test-trace',
    icon: CheckCircle2,
  },
] as const;

export default async function SettingsPage() {
  const keys = await listApiKeys('default') as unknown as ApiKeyRow[];

  return (
    <>
      <PageHeader
        eyebrow="Workspace"
        title="Settings & setup"
        description="Connect an application securely and verify real telemetry delivery in minutes."
      />

      <section aria-label="SDK setup" className="grid gap-3 md:grid-cols-2">
        {STEPS.map(({ title, description, command, icon: Icon }, index) => (
          <Card key={title}>
            <CardHeader>
              <div>
                <p className="mb-1 text-[10px] font-bold uppercase tracking-wider text-primary-accent">
                  Step {index + 1}
                </p>
                <CardTitle>{title}</CardTitle>
                <CardDescription>{description}</CardDescription>
              </div>
              <span className="grid size-8 shrink-0 place-items-center rounded-md bg-muted text-foreground-tertiary">
                <Icon size={16} />
              </span>
            </CardHeader>
            <CardContent>
              <pre className="overflow-x-auto rounded-md border border-border bg-code p-3 font-mono text-xs leading-5 text-foreground">
                <code>{command}</code>
              </pre>
            </CardContent>
          </Card>
        ))}
      </section>

      <div className="mt-4">
        <ApiKeys initialKeys={keys} />
      </div>
    </>
  );
}
