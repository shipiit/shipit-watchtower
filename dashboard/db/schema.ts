export const tableStatements = [
  // A project is the unit of tenancy: what an API key is scoped to, and what
  // every read is filtered by. `default` exists from the first request so a
  // single-project deployment never has to think about it.
  `CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
  )`,
  `INSERT INTO projects (id, name, created_at)
     VALUES ('default', 'default', unixepoch())
     ON CONFLICT(id) DO NOTHING`,
  // Only the hash is stored. A key readable from the database is a key that
  // leaks with a database backup, and there is no reason to be able to read
  // one back — the plaintext is shown once, at creation, or not at all.
  `CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    prefix TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_used_at REAL,
    revoked_at REAL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
  )`,
  `CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL,
    user_id TEXT,
    session_id TEXT,
    company_id TEXT,
    cost_center TEXT,
    channel TEXT,
    status TEXT NOT NULL DEFAULT 'success',
    input_json TEXT,
    output_json TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost REAL NOT NULL DEFAULT 0,
    quality REAL,
    started_at REAL NOT NULL,
    ended_at REAL NOT NULL,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS spans (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT 'default',
    trace_id TEXT NOT NULL,
    parent_id TEXT,
    name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'DEFAULT',
    status_message TEXT,
    input_json TEXT,
    output_json TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    provider TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost REAL NOT NULL DEFAULT 0,
    started_at REAL NOT NULL,
    ended_at REAL,
    FOREIGN KEY (trace_id) REFERENCES traces(id) ON DELETE CASCADE
  )`,
  `CREATE TABLE IF NOT EXISTS scores (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT 'default',
    trace_id TEXT NOT NULL,
    observation_id TEXT,
    name TEXT NOT NULL,
    value REAL,
    text_value TEXT,
    source TEXT NOT NULL,
    comment TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    FOREIGN KEY (trace_id) REFERENCES traces(id) ON DELETE CASCADE
  )`,
  `CREATE TABLE IF NOT EXISTS prompt_versions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    prompt_type TEXT NOT NULL,
    template_json TEXT NOT NULL,
    labels_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    config_json TEXT NOT NULL DEFAULT '{}',
    commit_message TEXT,
    created_at REAL NOT NULL,
    UNIQUE(project_id, name, version)
  )`,
  `CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    UNIQUE(project_id, name)
  )`,
  `CREATE TABLE IF NOT EXISTS dataset_items (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT 'default',
    dataset_id TEXT NOT NULL,
    input_json TEXT,
    expected_output_json TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    source_trace_id TEXT,
    source_observation_id TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY (dataset_id) REFERENCES datasets(id) ON DELETE CASCADE
  )`,
  `CREATE TABLE IF NOT EXISTS experiment_results (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT 'default',
    dataset_item_id TEXT NOT NULL,
    run_name TEXT NOT NULL,
    trace_id TEXT,
    description TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    UNIQUE(dataset_item_id, run_name),
    FOREIGN KEY (dataset_item_id) REFERENCES dataset_items(id) ON DELETE CASCADE
  )`
] as const;

/**
 * Indexes, created after the migrations below have widened the tables.
 *
 * They lead with `project_id` because every read is project-scoped, and an
 * index that does not start with the column in the WHERE clause is one the
 * planner will decline to use. Ordering matters: an index over a column an
 * ALTER has not added yet fails, and it would take the whole batch with it.
 */
export const indexStatements = [
  `CREATE INDEX IF NOT EXISTS idx_traces_project_started ON traces(project_id, started_at DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_spans_project_type ON spans(project_id, event_type, started_at DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_scores_project_name ON scores(project_id, name, created_at DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(hash)`,
  `CREATE INDEX IF NOT EXISTS idx_traces_started_at ON traces(started_at DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_traces_session_started ON traces(session_id, started_at DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_traces_user_started ON traces(user_id, started_at DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_traces_status_started ON traces(status, started_at DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_spans_trace_started ON spans(trace_id, started_at)`,
  `CREATE INDEX IF NOT EXISTS idx_scores_trace_name ON scores(trace_id, name)`,
  `CREATE INDEX IF NOT EXISTS idx_prompts_name_version ON prompt_versions(name, version DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_dataset_items_dataset ON dataset_items(dataset_id, created_at DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_experiments_run ON experiment_results(run_name, created_at DESC)`,
  // `event_type='generation'` is the hottest predicate in the app — it drives
  // the overview, the model timeline, latency percentiles, session and user
  // model tables, and /models. Without this every one of them scanned spans.
  `CREATE INDEX IF NOT EXISTS idx_spans_type_started ON spans(event_type, started_at DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_spans_model ON spans(model, provider)`,
  `CREATE INDEX IF NOT EXISTS idx_scores_name_source ON scores(name, source, created_at DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_scores_created ON scores(created_at DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_traces_cost_center ON traces(cost_center, started_at DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_traces_company ON traces(company_id, started_at DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_traces_name ON traces(name, started_at DESC)`,
] as const;


/**
 * Columns added after the first release. These run between the tables and the
 * indexes.
 *
 * `CREATE TABLE IF NOT EXISTS` cannot widen a table that already exists, and
 * `ALTER TABLE ADD COLUMN` throws once the column is there — so these run one
 * at a time, and a "duplicate column" is the success case on every run after
 * the first. Kept separate from `schemaStatements` for exactly that reason:
 * one failing statement must not roll back the batch that creates everything.
 */
export const migrationStatements = [
  `ALTER TABLE traces ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'`,
  `ALTER TABLE spans ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'`,
  `ALTER TABLE scores ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'`,
  `ALTER TABLE prompt_versions ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'`,
  `ALTER TABLE datasets ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'`,
  `ALTER TABLE dataset_items ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'`,
  `ALTER TABLE experiment_results ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'`,
] as const;
