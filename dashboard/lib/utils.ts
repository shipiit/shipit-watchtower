import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Merge class names, letting the caller's classes win.
 *
 * `clsx` handles conditionals; `twMerge` resolves Tailwind conflicts so
 * `cn('p-2', 'p-4')` is `p-4` rather than both. Without it, a variant's
 * padding and an override's padding both land in the class list and which one
 * applies depends on stylesheet order — which is not something a component
 * author can reason about.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Number formatting, in one place.
 *
 * Every one of these goes through `Intl` rather than `toFixed`, because
 * locale-correct grouping and unit display are not things worth hand-rolling
 * — and because a cost printed two different ways on two screens reads as two
 * different numbers.
 */

const usd = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  // Six, not two: a single LLM call routinely costs $0.000412, and rounding
  // that to $0.00 makes a cost table look like everything is free.
  maximumFractionDigits: 6,
});

const usdCompact = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const counts = new Intl.NumberFormat('en-US');

export const money = (value: number | null | undefined) => usd.format(value ?? 0);

/** For totals and axes, where six decimals would be noise. */
export const moneyCompact = (value: number | null | undefined) =>
  usdCompact.format(value ?? 0);

export const count = (value: number | null | undefined) => counts.format(value ?? 0);

/** Latency, tiered so the unit always suits the magnitude. */
export function duration(ms: number | null | undefined): string {
  const value = ms ?? 0;
  if (value < 1000) return `${Math.round(value)}ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(value < 10_000 ? 2 : 1)}s`;
  if (value < 3_600_000) return `${Math.floor(value / 60_000)}m ${Math.round((value % 60_000) / 1000)}s`;
  return `${(value / 3_600_000).toFixed(1)}h`;
}

/** `420 → 96 (Σ 516)` — the shape that shows the split and the total at once. */
export function tokens(input: number, output: number): string {
  return `${count(input)} → ${count(output)} (Σ ${count(input + output)})`;
}

export function relative(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return '—';
  const seconds = Math.floor(Date.now() / 1000 - epochSeconds);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604_800) return `${Math.floor(seconds / 86_400)}d ago`;
  return new Date(epochSeconds * 1000).toLocaleDateString();
}

export const timestamp = (epochSeconds: number | null | undefined) =>
  epochSeconds ? new Date(epochSeconds * 1000).toLocaleString() : '—';
