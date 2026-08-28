/** Datasets, their items, and experiment run links. */

import { database, decode, encode, ensureSchema } from '@/lib/db';
import { randomId } from './shared';

export async function createDatasetRecord(payload: Record<string, unknown>) {
  await ensureSchema();
  const record = { id: randomId(), name: payload.name, description: payload.description ?? null, metadata: payload.metadata ?? {}, created_at: Date.now() / 1000 };
  // Insert-then-read rather than read-then-insert: `create_dataset` is called
  // unconditionally on every capture, so two turns racing on a new dataset
  // both saw "not there" and the loser hit UNIQUE(name) with a 500.
  await database().prepare(`INSERT INTO datasets (id,name,description,metadata_json,created_at)
    VALUES (?,?,?,?,?) ON CONFLICT(name) DO NOTHING`).bind(
    record.id, record.name, record.description, encode(record.metadata) ?? '{}', record.created_at,
  ).run();
  const stored = await database().prepare('SELECT * FROM datasets WHERE name=?').bind(payload.name).first();
  return stored ?? record;
}

export async function listDatasets() {
  await ensureSchema();
  // COUNT(DISTINCT …): the experiment_results join multiplies each item row
  // by its run count, so a 10-item dataset with 3 runs each reported 30.
  const result = await database().prepare(`SELECT d.*, COUNT(DISTINCT i.id) items,
    COUNT(DISTINCT e.run_name) runs, MAX(i.created_at) last_item_at
    FROM datasets d LEFT JOIN dataset_items i ON i.dataset_id=d.id
    LEFT JOIN experiment_results e ON e.dataset_item_id=i.id
    GROUP BY d.id ORDER BY d.created_at DESC`).all();
  return result.results;
}

export async function getDatasetRecord(name: string) {
  await ensureSchema();
  const db = database();
  const dataset = await db.prepare('SELECT * FROM datasets WHERE name=?').bind(name).first();
  if (!dataset) return null;
  const items = await db.prepare(`SELECT i.*, COUNT(e.id) experiment_runs
    FROM dataset_items i
    LEFT JOIN experiment_results e ON e.dataset_item_id = i.id
    WHERE i.dataset_id = ?
    GROUP BY i.id ORDER BY i.created_at DESC`).bind(dataset.id).all();

  return {
    ...dataset,
    metadata: decode<Record<string, unknown>>(String(dataset.metadata_json ?? '{}'), {}),
    items: items.results.map((item) => ({
      ...item,
      input: decode(String(item.input_json), null),
      expected_output: decode(String(item.expected_output_json), null),
      metadata: decode<Record<string, unknown>>(String(item.metadata_json), {}),
    })),
  };
}

export async function createDatasetItem(payload: Record<string, unknown>) {
  await ensureSchema();
  const dataset = await database().prepare('SELECT id FROM datasets WHERE name=?').bind(payload.dataset_name).first<{ id: string }>();
  if (!dataset) throw new Error('dataset not found');
  const id = typeof payload.id === 'string' && payload.id ? payload.id : randomId();
  const createdAt = Date.now() / 1000;
  await database().prepare(`INSERT INTO dataset_items (id,dataset_id,input_json,
    expected_output_json,metadata_json,source_trace_id,source_observation_id,created_at)
    VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET input_json=excluded.input_json,
    expected_output_json=excluded.expected_output_json,metadata_json=excluded.metadata_json`).bind(
    id, dataset.id, encode(payload.input), encode(payload.expected_output),
    encode(payload.metadata ?? {}) ?? '{}', payload.source_trace_id ?? null,
    payload.source_observation_id ?? null, createdAt,
  ).run();
  return { id };
}

export async function linkExperiment(payload: Record<string, unknown>) {
  await ensureSchema();
  const id = randomId();
  await database().prepare(`INSERT INTO experiment_results (id,dataset_item_id,run_name,
    trace_id,description,metadata_json,created_at) VALUES (?,?,?,?,?,?,?)
    ON CONFLICT(dataset_item_id,run_name) DO UPDATE SET trace_id=excluded.trace_id,
    description=excluded.description,metadata_json=excluded.metadata_json`).bind(
    id, payload.dataset_item_id, payload.run_name, payload.trace_id ?? null,
    payload.description ?? null, encode(payload.metadata ?? {}) ?? '{}', Date.now() / 1000,
  ).run();
  return { id };
}
