'use client';

import { Check, Copy, KeyRound, Plus, Trash2 } from 'lucide-react';
import { useCallback, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { dateOnly } from '@/lib/utils';

export interface ApiKeyRow {
  id: string;
  name: string;
  prefix: string;
  created_at: number;
  last_used_at: number | null;
  revoked_at: number | null;
}

const when = (value: number | null) =>
  dateOnly(value);

export function ApiKeys(
  { project = 'default', initialKeys = [] }:
  { project?: string; initialKeys?: ApiKeyRow[] },
) {
  // Rendered from the server on first paint, refetched only after a mutation.
  // Loading the list in an effect meant an empty table for one frame and a
  // second round-trip for data the page already had.
  const [keys, setKeys] = useState<ApiKeyRow[]>(initialKeys);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Held in state only until the page is left. This is the one and only time
  // the plaintext exists outside the caller's environment — the database
  // stores a hash, so there is nothing to show a second time.
  const [issued, setIssued] = useState<{ key: string; name: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await fetch(`/api/keys?project=${encodeURIComponent(project)}`);
      if (!response.ok) throw new Error(String(response.status));
      const body = (await response.json()) as { keys: ApiKeyRow[] };
      setKeys(body.keys ?? []);
    } catch {
      setError('could not load keys');
    }
  }, [project]);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const response = await fetch('/api/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, project }),
      });
      if (!response.ok) throw new Error(String(response.status));
      const created = (await response.json()) as { key: string; name: string };
      setIssued(created);
      setName('');
      await load();
    } catch {
      setError('could not create the key');
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    setError('');
    try {
      const response = await fetch(`/api/keys?id=${encodeURIComponent(id)}`, { method: 'DELETE' });
      if (!response.ok) throw new Error(String(response.status));
      await load();
    } catch {
      setError('could not revoke the key');
    }
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>API keys</CardTitle>
          <CardDescription>
            Scoped to <strong className="text-foreground">{project}</strong>. A key writes only into its own project.
          </CardDescription>
        </div>
        <span className="grid size-8 place-items-center rounded-md bg-muted text-foreground-tertiary">
          <KeyRound size={16} />
        </span>
      </CardHeader>

      <CardContent className="space-y-3">

      {issued ? (
        <div className="rounded-md border border-primary-accent/30 bg-primary-accent/5 p-3" role="status">
          <p className="text-xs text-muted-foreground">
            Copy <strong>{issued.name}</strong> now — it is stored as a hash and
            cannot be shown again.
          </p>
          <div className="mt-2 flex min-w-0 items-center gap-2">
            <code className="min-w-0 flex-1 overflow-x-auto rounded border border-border bg-code p-2 font-mono text-xs">
              {issued.key}
            </code>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                void navigator.clipboard.writeText(issued.key);
                setCopied(true);
                setTimeout(() => setCopied(false), 1200);
              }}
            >
              {copied ? <Check size={14} /> : <Copy size={14} />}
              {copied ? 'Copied' : 'Copy'}
            </Button>
          </div>
          <pre className="mt-2 overflow-x-auto rounded border border-border bg-code p-2 font-mono text-xs">{`WATCHER_DASHBOARD_TOKEN=${issued.key}`}</pre>
          <Button type="button" variant="ghost" size="sm" className="mt-2" onClick={() => setIssued(null)}>
            I have saved it
          </Button>
        </div>
      ) : null}

      <form className="flex flex-col gap-2 sm:flex-row" onSubmit={create}>
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="What is this key for? e.g. production-api"
          aria-label="Key name"
        />
        <Button type="submit" variant="accent" disabled={busy || !name.trim()}>
          <Plus size={14} />Create key
        </Button>
      </form>

      {error ? <p className="text-xs text-destructive" role="alert">{error}</p> : null}

      {keys.length ? (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full min-w-[620px] text-left text-xs">
            <thead className="border-b border-border bg-muted/40 text-foreground-tertiary">
              <tr><th className="p-2.5">Name</th><th>Key</th><th>Created</th><th>Last used</th><th><span className="sr-only">Actions</span></th></tr>
            </thead>
            <tbody className="divide-y divide-border">
              {keys.map((key) => (
                <tr className={key.revoked_at ? 'text-muted-foreground opacity-60' : ''} key={key.id}>
                  <td className="p-2.5 font-bold text-foreground">{key.name}</td>
                  <td><code className="font-mono">{key.prefix}…</code></td>
                  <td>{when(key.created_at)}</td>
                  <td>{key.revoked_at ? 'revoked' : when(key.last_used_at)}</td>
                  <td className="pr-2 text-right">
                    {key.revoked_at ? null : (
                      <Button variant="ghost" size="icon-sm" type="button" onClick={() => revoke(key.id)} aria-label={`Revoke ${key.name}`}>
                        <Trash2 size={13} />
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="rounded-md border border-dashed border-border p-4 text-center text-xs text-muted-foreground">
          No keys yet. Until one exists, ingestion falls back to the shared
          <code> WATCHER_INGEST_KEY</code>, which cannot say which project it is for.
        </p>
      )}
      </CardContent>
    </Card>
  );
}
