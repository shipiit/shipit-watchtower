export interface IngestEvent {
  id: string;
  parent_id?: string | null;
  name: string;
  event_type: string;
  severity?: string;
  status_message?: string;
  input?: unknown;
  output?: unknown;
  started_at: number;
  ended_at?: number | null;
  duration_ms?: number;
  model?: string;
  provider?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  total_cost?: number;
  [key: string]: unknown;
}

export interface IngestTrace {
  schema_version: 'watcher.trace.v1';
  /** Tenancy scope. Absent on bundles from an SDK older than this field. */
  project?: string;
  trace_id: string;
  name: string;
  input?: unknown;
  output?: unknown;
  context?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  events: IngestEvent[];
}

export interface TraceRow {
  id: string;
  name: string;
  user_id: string | null;
  session_id: string | null;
  company_id: string | null;
  cost_center: string | null;
  channel: string | null;
  status: string;
  input_json: string | null;
  output_json: string | null;
  metadata_json: string;
  duration_ms: number;
  total_tokens: number;
  total_cost: number;
  observation_count: number;
  quality: number | null;
  started_at: number;
  ended_at: number;
  received_at: string;
}

export interface SpanRow {
  id: string;
  trace_id: string;
  parent_id: string | null;
  name: string;
  event_type: string;
  severity: string;
  status_message: string | null;
  input_json: string | null;
  output_json: string | null;
  metadata_json: string;
  duration_ms: number;
  model: string | null;
  provider: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  total_cost: number;
  started_at: number;
  ended_at: number | null;
}

export interface ObservationRow extends SpanRow {
  trace_name: string;
  trace_status: string;
  user_id: string | null;
  session_id: string | null;
}

export interface TraceSpan extends SpanRow {
  input: unknown;
  output: unknown;
  metadata: Record<string, unknown>;
}

export interface TraceScore {
  id: string;
  trace_id: string;
  observation_id: string | null;
  name: string;
  value: number | null;
  text_value: string | null;
  source: string;
  comment: string | null;
  metadata_json: string;
  created_at: number;
}

export interface TraceDetail extends TraceRow {
  input: unknown;
  output: unknown;
  metadata: Record<string, unknown>;
  spans: TraceSpan[];
  scores: TraceScore[];
}
