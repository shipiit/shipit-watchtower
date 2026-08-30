import { Download } from 'lucide-react';
import Link from 'next/link';
import { PageHeader } from '@/components/page-header';
import { TraceBrowser } from '@/components/trace-browser';
import { queryObservations, queryTraces, type TraceFilters } from '@/lib/repository';
import type { ObservationRow, TraceRow } from '@/lib/types';

export const dynamic = 'force-dynamic';

type Params = Record<string, string | string[] | undefined>;
const value = (params: Params, key: string) => typeof params[key] === 'string' ? params[key] : '';
const number = (params: Params, key: string) => value(params, key) ? Number(value(params, key)) : undefined;
const rangeSeconds: Record<string, number> = { '30m': 1800, '1h': 3600, '6h': 21600, '1d': 86400, '3d': 259200, '7d': 604800, '14d': 1209600, '30d': 2592000, '90d': 7776000 };

export default async function TracesPage({ searchParams }: { searchParams: Promise<Params> }) {
  const params = await searchParams;
  const active = Object.fromEntries(Object.entries(params).filter((entry): entry is [string, string] => typeof entry[1] === 'string'));
  const page = Math.max(1, number(params, 'page') ?? 1);
  const limit = Math.min(200, Math.max(25, number(params, 'limit') ?? 50));
  const range = value(params, 'range') || '7d';
  const customStart = value(params, 'start') ? new Date(value(params, 'start')).getTime() / 1000 : undefined;
  const customEnd = value(params, 'end') ? new Date(value(params, 'end')).getTime() / 1000 : undefined;
  const filters: TraceFilters = {
    limit, offset: (page - 1) * limit, search: value(params, 'search'), status: value(params, 'status'), user: value(params, 'user'), session: value(params, 'session'), model: value(params, 'model'), provider: value(params, 'provider'), name: value(params, 'name'), traceId: value(params, 'traceId'), company: value(params, 'company'), costCenter: value(params, 'costCenter'), channel: value(params, 'channel'), metadata: value(params, 'metadata'), eventType: value(params, 'eventType'), minLatency: number(params, 'minLatency'), maxLatency: number(params, 'maxLatency'), minTokens: number(params, 'minTokens'), maxTokens: number(params, 'maxTokens'), minCost: number(params, 'minCost'), maxCost: number(params, 'maxCost'), minObservations: number(params, 'minObservations'), maxObservations: number(params, 'maxObservations'), minQuality: number(params, 'minQuality'), since: customStart, within: customStart ? undefined : rangeSeconds[range], until: customEnd,
  };
  const view = value(params, 'view') === 'observations' ? 'observations' : 'traces';
  const result = await (view === 'traces' ? queryTraces(filters) : queryObservations(filters));
  const traces = view === 'traces' ? result.rows as TraceRow[] : [];
  const observations = view === 'observations' ? result.rows as ObservationRow[] : [];
  const query = new URLSearchParams(active).toString();
  return <>
    <PageHeader eyebrow="Observe" title="Traces" description="Inspect every agent run, generation, tool, retrieval, policy, and evaluation." actions={<Link className="secondary-button" href={`/api/traces?limit=200&${query}`}><Download size={14}/>JSON API</Link>}/>
    <TraceBrowser traces={traces} observations={observations} total={result.total} page={page} limit={limit} view={view} active={{ ...active, range }}/>
  </>;
}
