#!/usr/bin/env node
/**
 * One command to get the Watcher dashboard running.
 *
 *     npm run setup
 *
 * Installs dependencies if they are missing, generates an ingest secret, and
 * prints the exact environment the SDK side needs. It is idempotent: run it
 * again and it reports the same secret rather than rotating it, because a
 * setup script that silently invalidates a running deployment's credentials
 * is worse than one you have to read twice.
 *
 * There is deliberately no migration step. The schema creates itself on first
 * database access (`lib/db.ts` -> `ensureSchema`), so "set up the database" is
 * not something anyone has to remember to do.
 */

import { execSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes } from 'node:crypto';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const devVars = join(root, '.dev.vars');

const step = (message) => process.stdout.write(`  - ${message}\n`);

// -- node version -----------------------------------------------------------
const [major] = process.versions.node.split('.').map(Number);
if (major < 22) {
  console.error(
    `\nThis dashboard needs Node 22 or newer (found ${process.versions.node}).\n` +
      `Cloudflare's local Workers runtime does not run on older versions.\n`,
  );
  process.exit(1);
}

// -- dependencies -----------------------------------------------------------
if (!existsSync(join(root, 'node_modules'))) {
  step('installing dependencies (the slow part, once)');
  execSync('npm install', { cwd: root, stdio: 'inherit' });
} else {
  step('dependencies already installed');
}

// -- ingest secret ----------------------------------------------------------
// Read an existing value back rather than minting a new one: rotating the key
// under a running deployment turns every ingest into a silent 401.
let key = '';
if (existsSync(devVars)) {
  const match = readFileSync(devVars, 'utf8').match(/^WATCHER_INGEST_KEY=(.*)$/m);
  key = match?.[1]?.trim() ?? '';
}
if (key) {
  step('reusing the ingest key already in .dev.vars');
} else {
  key = randomBytes(32).toString('base64url');
  const existing = existsSync(devVars)
    ? `${readFileSync(devVars, 'utf8').trimEnd()}\n`
    : '';
  writeFileSync(devVars, `${existing}WATCHER_INGEST_KEY=${key}\n`, { mode: 0o600 });
  step('generated an ingest key into .dev.vars');
}

// -- report -----------------------------------------------------------------
console.log(`
The dashboard is ready.

  npm run dev       ->  http://localhost:3000
  npm run deploy    ->  Cloudflare, with a real D1 database

Point your application at it. The same secret goes on both sides:

  WATCHER_DASHBOARD_URL=http://localhost:3000
  WATCHER_DASHBOARD_TOKEN=${key}

Then, in the application:

  import shipit_watcher as wt
  wt.setup(service_name="my-app")

That is the whole integration. Whatever model SDK you already use is
instrumented automatically, and traces start arriving here.

.dev.vars holds a secret. It is gitignored; keep it that way.
`);
