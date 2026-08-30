/** What the trace table can show, and how it is laid out. */

export const COLUMNS = [
  'timestamp', 'name', 'type', 'input', 'output', 'observations', 'latency',
  'tokens', 'cost', 'model', 'provider', 'user', 'session', 'quality', 'status',
] as const;

export type Column = (typeof COLUMNS)[number];

export const DEFAULT_COLUMNS: Column[] = [
  'timestamp', 'name', 'type', 'input', 'output', 'latency', 'tokens', 'cost', 'status',
];

export const RANGES: Array<[string, string]> = [
  ['1h', 'Past hour'], ['1d', 'Past day'], ['7d', 'Past 7 days'],
  ['30d', 'Past 30 days'], ['90d', 'Past 90 days'], ['all', 'All time'],
];

/** Row heights, so one table can be a dense list or a readable preview. */
export const DENSITY = { s: 'h-7', m: 'h-16', l: 'h-32' } as const;
export type Density = keyof typeof DENSITY;

export const DENSITY_LABELS: Record<Density, string> = {
  s: 'Compact', m: 'Comfortable', l: 'Tall',
};

export const COLUMN_KEY = 'watcher.trace-columns';
export const DENSITY_KEY = 'watcher.trace-density';

/** Build a `/traces` URL from the active filters plus some changes. */
export function href(
  active: Record<string, string>,
  updates: Record<string, string | number>,
) {
  const query = new URLSearchParams(active);
  for (const [key, value] of Object.entries(updates)) {
    if (value === '') query.delete(key);
    else query.set(key, String(value));
  }
  return `/traces?${query.toString()}`;
}

export function parseColumns(raw: string): Column[] | null {
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    return parsed.filter((name): name is Column => COLUMNS.includes(name));
  } catch {
    return null;
  }
}

export function parseDensity(raw: string): Density | null {
  const value = raw.replaceAll('"', '');
  return value === 's' || value === 'm' || value === 'l' ? value : null;
}
