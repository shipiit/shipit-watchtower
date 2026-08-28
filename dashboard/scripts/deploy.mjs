#!/usr/bin/env node
/**
 * One command to put the Watcher dashboard on Cloudflare.
 *
 *     npm run deploy
 *
 * Creates the D1 database if it does not exist, builds, rewrites the
 * generated Worker config to point at the real database instead of the
 * scaffold's placeholder id, uploads the ingest secret, and deploys.
 *
 * Every step is idempotent, so this is also the redeploy command.
 *
 * It refuses rather than guesses. A deploy script that carries on past a
 * failed database lookup ships a Worker bound to a database that is not
 * there, and the first symptom is a 500 on the first trace — long after the
 * command reported success.
 */

import { execFileSync, execSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes } from 'node:crypto';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const devVars = join(root, '.dev.vars');
const DATABASE_NAME = process.env.WATCHER_D1_NAME || 'watcher';
const PLACEHOLDER_ID = '00000000-0000-4000-8000-000000000000';

const step = (message) => process.stdout.write(`  - ${message}\n`);

function fail(message, hint) {
  console.error(`\n${message}\n${hint ? `\n  ${hint}\n` : ''}`);
  process.exit(1);
}

function wrangler(args, { capture = true } = {}) {
  return execFileSync('npx', ['wrangler', ...args], {
    cwd: root,
    encoding: 'utf8',
    stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
  });
}

// -- account ----------------------------------------------------------------
try {
  wrangler(['whoami']);
} catch {
  fail(
    'Not signed in to Cloudflare.',
    'Run: npx wrangler login   (then run npm run deploy again)',
  );
}
step('signed in to Cloudflare');

// -- database ---------------------------------------------------------------
// Looked up by name every time rather than cached in a file: a cached id that
// no longer exists is the one failure mode that produces a green deploy and a
// broken dashboard.
let databaseId = '';
try {
  const databases = JSON.parse(wrangler(['d1', 'list', '--json']) || '[]');
  databaseId = databases.find((entry) => entry.name === DATABASE_NAME)?.uuid ?? '';
} catch {
  fail('Could not list D1 databases.', 'Check `npx wrangler d1 list` for the real error.');
}

if (databaseId) {
  step(`using the existing D1 database "${DATABASE_NAME}"`);
} else {
  step(`creating the D1 database "${DATABASE_NAME}"`);
  try {
    const created = wrangler(['d1', 'create', DATABASE_NAME, '--json']);
    databaseId = JSON.parse(created)?.uuid ?? JSON.parse(created)?.database_id ?? '';
  } catch (error) {
    fail(`Could not create the D1 database "${DATABASE_NAME}".`, String(error.stderr || error));
  }
  if (!databaseId) fail('Cloudflare created the database but returned no id.');
}

// -- build ------------------------------------------------------------------
step('building');
execSync('npm run build', { cwd: root, stdio: 'inherit' });

// The scaffold binds a placeholder database id for local Miniflare. Rewrite
// the generated config so the deployed Worker talks to the real database.
const generated = join(root, 'dist', 'server', 'wrangler.json');
if (!existsSync(generated)) {
  fail('The build produced no Worker config.', `Expected ${generated}`);
}
const config = JSON.parse(readFileSync(generated, 'utf8'));
const bindings = config.d1_databases ?? [];
if (!bindings.length) fail('The build produced no D1 binding to point at the database.');
for (const binding of bindings) {
  if (binding.database_id && binding.database_id !== PLACEHOLDER_ID) continue;
  binding.database_name = DATABASE_NAME;
  binding.database_id = databaseId;
}
config.name = process.env.WATCHER_WORKER_NAME || config.name || 'watcher-dashboard';
writeFileSync(generated, JSON.stringify(config, null, 2));
step(`bound ${bindings.map((b) => b.binding).join(', ')} to "${DATABASE_NAME}"`);

// -- secret -----------------------------------------------------------------
let key = '';
if (existsSync(devVars)) {
  key = readFileSync(devVars, 'utf8').match(/^WATCHER_INGEST_KEY=(.*)$/m)?.[1]?.trim() ?? '';
}
if (!key) {
  key = randomBytes(32).toString('base64url');
  const existing = existsSync(devVars)
    ? `${readFileSync(devVars, 'utf8').trimEnd()}\n`
    : '';
  writeFileSync(devVars, `${existing}WATCHER_INGEST_KEY=${key}\n`, { mode: 0o600 });
  step('generated an ingest key into .dev.vars');
}
try {
  execSync(`npx wrangler secret put WATCHER_INGEST_KEY --config ${JSON.stringify(generated)}`, {
    cwd: root,
    input: `${key}\n`,
    stdio: ['pipe', 'ignore', 'inherit'],
  });
  step('uploaded the ingest secret');
} catch {
  // Non-fatal, and worth saying out loud: without the secret the deployment
  // fails closed and refuses every trace, which reads like an SDK bug.
  console.warn(
    '\n  ! Could not upload WATCHER_INGEST_KEY. Ingestion will 401 until you run:\n' +
      `      npx wrangler secret put WATCHER_INGEST_KEY --config ${generated}\n`,
  );
}

// -- deploy -----------------------------------------------------------------
step('deploying');
execSync(`npx wrangler deploy --config ${JSON.stringify(generated)}`, {
  cwd: root,
  stdio: 'inherit',
});

console.log(`
Deployed.

Point your application at the URL Wrangler printed above:

  WATCHER_DASHBOARD_URL=https://<the-url-above>
  WATCHER_DASHBOARD_TOKEN=${key}

The schema creates itself on the first trace, so there is nothing to migrate.
`);
