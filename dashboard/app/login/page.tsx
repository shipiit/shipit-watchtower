'use client';

import { LogIn, ShieldCheck } from 'lucide-react';
import { useState } from 'react';

export default function LoginPage() {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as { error?: string };
        setError(body.error ?? 'could not sign in');
        return;
      }
      // Read the destination here rather than on the server: the middleware
      // put it in the query string precisely so signing in returns you to the
      // page you asked for instead of the overview.
      const next = new URLSearchParams(window.location.search).get('next');
      window.location.href = next && next.startsWith('/') ? next : '/';
    } catch {
      setError('could not reach the dashboard');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="panel login-card" onSubmit={submit}>
        <i className="login-mark"><ShieldCheck size={20} /></i>
        <h1>Shipit Watcher</h1>
        <p>Traces carry whatever your prompts and completions carried. Sign in to read them.</p>
        <label htmlFor="password">Dashboard password</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="••••••••••••"
        />
        {error ? <p className="login-error" role="alert">{error}</p> : null}
        <button type="submit" disabled={busy || !password}>
          <LogIn size={15} />{busy ? 'Signing in…' : 'Sign in'}
        </button>
        <small>
          Set with <code>npx wrangler secret put WATCHER_DASHBOARD_PASSWORD</code>
        </small>
      </form>
    </div>
  );
}
