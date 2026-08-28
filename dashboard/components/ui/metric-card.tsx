import { ArrowDownRight, ArrowUpRight, Minus, type LucideIcon } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';

interface Props {
  label: string;
  value: string;
  detail?: string;
  icon: LucideIcon;
  /** Points for the sparkline, oldest first. Fewer than two draws nothing. */
  trend?: number[];
  /** Whether a rise is good. Cost and latency rising is not an improvement. */
  higherIsBetter?: boolean;
}

/**
 * A number, what it is doing, and what it was.
 *
 * A bare figure answers "what is it" and nothing else — you cannot tell
 * whether $0.14 is a quiet day or a fire. The sparkline gives it a shape and
 * the delta gives it a direction, both from data the page already has, so
 * neither costs a query.
 */
export function MetricCard({
  label, value, detail, icon: Icon, trend = [], higherIsBetter = true,
}: Props) {
  const delta = changeOver(trend);

  return (
    <Card className="group relative overflow-hidden p-4 transition-shadow hover:shadow-lift-md">
      <div className="flex items-center gap-2 text-muted-foreground">
        <Icon size={14} />
        <p className="text-xs font-bold">{label}</p>
        {delta === null ? null : <DeltaBadge delta={delta} higherIsBetter={higherIsBetter} />}
      </div>

      <p className="mt-2 text-2xl font-bold tabular-nums text-foreground">{value}</p>
      {detail ? <p className="mt-0.5 text-xs text-foreground-tertiary">{detail}</p> : null}

      {trend.length > 1 ? <Sparkline points={trend} /> : null}
    </Card>
  );
}

function DeltaBadge({ delta, higherIsBetter }: { delta: number; higherIsBetter: boolean }) {
  const flat = Math.abs(delta) < 1;
  const good = higherIsBetter ? delta > 0 : delta < 0;
  const Icon = flat ? Minus : delta > 0 ? ArrowUpRight : ArrowDownRight;

  return (
    <span
      className={cn(
        'ml-auto inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-xs font-bold',
        flat ? 'bg-muted text-muted-foreground'
          : good ? 'bg-light-green text-dark-green'
            : 'bg-light-red text-dark-red',
      )}
    >
      <Icon size={11} />
      {flat ? 'flat' : `${Math.abs(Math.round(delta))}%`}
    </span>
  );
}

/**
 * A bare path, no axes.
 *
 * It is deliberately unlabelled: at this size a sparkline communicates shape,
 * and adding ticks to it would imply a precision the 40px height cannot
 * deliver. The exact numbers live in the chart below.
 */
function Sparkline({ points }: { points: number[] }) {
  const max = Math.max(...points);
  const min = Math.min(...points);
  const span = max - min || 1;
  const step = 100 / Math.max(1, points.length - 1);

  const path = points
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${index * step} ${28 - ((point - min) / span) * 26}`)
    .join(' ');

  return (
    <svg
      viewBox="0 0 100 30"
      preserveAspectRatio="none"
      className="mt-3 h-8 w-full text-primary-accent"
      aria-hidden
    >
      <path d={`${path} L 100 30 L 0 30 Z`} fill="currentColor" opacity="0.08" />
      <path d={path} fill="none" stroke="currentColor" strokeWidth="1.5"
        vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * Percentage change between the first and second half of the window.
 *
 * Comparing halves rather than first-vs-last point, because a single quiet
 * hour at either end would otherwise read as a collapse. `null` when there is
 * not enough data to say anything — an unknown trend is not a flat one.
 */
function changeOver(points: number[]): number | null {
  if (points.length < 4) return null;
  const middle = Math.floor(points.length / 2);
  const mean = (values: number[]) =>
    values.reduce((sum, value) => sum + value, 0) / (values.length || 1);
  const before = mean(points.slice(0, middle));
  const after = mean(points.slice(middle));
  if (!before) return after ? 100 : null;
  return ((after - before) / before) * 100;
}
