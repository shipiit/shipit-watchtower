/**
 * Kept as a thin re-export while pages migrate.
 *
 * Formatting now lives in `lib/utils`, where the whole app can reach it
 * without importing from a component directory.
 */
export { count as integer, duration, money, relative, timestamp } from '@/lib/utils';

/** Percent, for the pages that still ask for it in this shape. */
export const percent = (value: number) => `${(value * 100).toFixed(1)}%`;
