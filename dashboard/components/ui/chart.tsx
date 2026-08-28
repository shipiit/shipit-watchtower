'use client';

import {
  Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip,
  XAxis, YAxis,
} from 'recharts';
import { cn, count, duration, money, moneyCompact } from '@/lib/utils';

/**
 * The one chart wrapper.
 *
 * Three rules from the charting spec are enforced here rather than left to
 * each call site:
 *
 * **Missing is a gap, not a zero.** A bucket with no data is `null`, so the
 * line breaks instead of drawing a confident dive to the x-axis. Only
 * additive metrics get to treat absence as zero, and that is the caller's
 * decision to declare.
 *
 * **Colour is identity.** A series keeps its colour across every chart on the
 * page, taken from its index in a stable, bounded palette — not from the
 * order the data happened to arrive in.
 *
 * **Spend ink on data.** Faint gridlines, no axis spine, no dots at rest, no
 * entry animation. Recharts' defaults draw a white stroke around every dot
 * and a `#ccc` grid, neither of which survives a dark theme, so both are
 * overridden.
 */

const PALETTE = [
  'var(--color-chart-1)', 'var(--color-chart-2)', 'var(--color-chart-3)',
  'var(--color-chart-4)', 'var(--color-chart-5)', 'var(--color-chart-6)',
  'var(--color-chart-7)', 'var(--color-chart-8)',
];

export const seriesColor = (index: number) => PALETTE[index % PALETTE.length];

/**
 * Formatters by name, not by function.
 *
 * A server component cannot hand a function to a client one — React has no
 * way to serialise it across the boundary, and the page dies with "Functions
 * cannot be passed directly to Client Components". Naming the formatter keeps
 * the prop a string, which crosses fine, and has the side benefit that two
 * charts asking for `money` cannot format it differently.
 */
const FORMATS = {
  number: (value: number) => count(Math.round(value)),
  money: (value: number) => money(value),
  cost: (value: number) => moneyCompact(value),
  duration: (value: number) => duration(value),
  score: (value: number) => value.toFixed(2),
} as const;

export type ChartFormat = keyof typeof FORMATS;

export interface ChartPoint {
  bucket: string;
  [series: string]: string | number | null;
}

export function TimeSeriesChart({
  data,
  series,
  format = 'number',
  height = 220,
  className,
  emptyMessage = 'No telemetry in this range.',
}: {
  data: ChartPoint[];
  series: string[];
  format?: ChartFormat;
  height?: number;
  className?: string;
  emptyMessage?: string;
}) {
  const formatValue = FORMATS[format] ?? FORMATS.number;

  if (!data.length || !series.length) {
    return (
      <div
        className={cn(
          'flex items-center justify-center rounded-md border border-dashed border-border',
          'text-xs text-muted-foreground',
          className,
        )}
        style={{ height }}
      >
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className={className} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
          <defs>
            {series.map((name, index) => (
              <linearGradient key={name} id={`fill-${index}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={seriesColor(index)} stopOpacity={0.28} />
                <stop offset="100%" stopColor={seriesColor(index)} stopOpacity={0.02} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid
            vertical={false}
            stroke="var(--color-border)"
            strokeDasharray="3 3"
          />
          <XAxis
            dataKey="bucket"
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 10, fill: 'var(--color-muted-foreground)' }}
            minTickGap={24}
          />
          <YAxis
            tickLine={false}
            axisLine={false}
            width={56}
            tick={{ fontSize: 10, fill: 'var(--color-muted-foreground)' }}
            tickFormatter={(value: number) => formatValue(value)}
          />
          <Tooltip
            cursor={{ stroke: 'var(--color-border-contrast)' }}
            contentStyle={{
              background: 'var(--color-popover)',
              border: '1px solid var(--color-border)',
              borderRadius: 6,
              fontSize: 11,
              boxShadow: 'none',
            }}
            labelStyle={{ color: 'var(--color-muted-foreground)', fontSize: 10 }}
            formatter={(value, name) => [formatValue(Number(value ?? 0)), String(name)]}
          />
          {series.length > 1 ? (
            <Legend
              iconType="circle"
              iconSize={7}
              wrapperStyle={{ fontSize: 11, paddingTop: 6 }}
            />
          ) : null}
          {series.map((name, index) => (
            <Area
              key={name}
              type="monotone"
              dataKey={name}
              stroke={seriesColor(index)}
              strokeWidth={2}
              fill={`url(#fill-${index})`}
              // A gap in the data is drawn as a gap, not joined across.
              connectNulls={false}
              dot={false}
              activeDot={{ r: 3, strokeWidth: 0 }}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
