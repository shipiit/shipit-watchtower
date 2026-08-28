'use client';

import { Check, Copy, KeyRound, Plus, Trash2 } from 'lucide-react';
import { useCallback, useState } from 'react';

export interface ApiKeyRow {
  id: string;
  name: string;
  prefix: string;
  created_at: number;
  last_used_at: number | null;
  revoked_at: number | null;
}

const when = (value: number | null) =>
  value ? new Date(value * 1000).toLocaleDateString() : '—';

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
      await fetch(`/api/keys?id=${encodeURIComponent(id)}`, { method: 'DELETE' });
      await load();
    } catch {
      setError('could not revoke the key');
    }
  }

  return (
    <article className="panel api-keys">
      <div className="panel-title">
        <div>
          <h2>API keys</h2>
          <p>Scoped to <strong>{project}</strong>. A key writes only into its own project.</p>
        </div>
        <KeyRound size={17} />
      </div>

      {issued ? (
        <div className="key-issued" role="status">
          <p>
            Copy <strong>{issued.name}</strong> now — it is stored as a hash and
            cannot be shown again.
          </p>
          <div>
            <code>{issued.key}</code>
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard.writeText(issued.key);
                setCopied(true);
                setTimeout(() => setCopied(false), 1200);
              }}
            >
              {copied ? <Check size={14} /> : <Copy size={14} />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          <pre>{`WATCHER_DASHBOARD_TOKEN=${issued.key}`}</pre>
          <button type="button" className="dismiss" onClick={() => setIssued(null)}>
            I have saved it
          </button>
        </div>
      ) : null}

      <form className="key-create" onSubmit={create}>
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="What is this key for? e.g. production-api"
          aria-label="Key name"
        />
        <button type="submit" disabled={busy || !name.trim()}>
          <Plus size={14} />Create key
        </button>
      </form>

      {error ? <p className="login-error">{error}</p> : null}

      {keys.length ? (
        <div className="key-table">
          <div className="key-row heading">
            <span>Name</span><span>Key</span><span>Created</span><span>Last used</span><span />
          </div>
          {keys.map((key) => (
            <div className={`key-row ${key.revoked_at ? 'revoked' : ''}`} key={key.id}>
              <span>{key.name}</span>
              <code>{key.prefix}…</code>
              <span>{when(key.created_at)}</span>
              {/* An unused key is the easiest one to decide about. */}
              <span>{key.revoked_at ? 'revoked' : when(key.last_used_at)}</span>
              <span>
                {key.revoked_at ? null : (
                  <button type="button" onClick={() => revoke(key.id)} aria-label={`Revoke ${key.name}`}>
                    <Trash2 size={13} />
                  </button>
                )}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="key-empty">
          No keys yet. Until one exists, ingestion falls back to the shared
          <code> WATCHER_INGEST_KEY</code>, which cannot say which project it is for.
        </p>
      )}
    </article>
  );
}
