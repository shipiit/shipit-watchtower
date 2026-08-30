import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface Stat {
  label: string;
  value: React.ReactNode;
  icon?: LucideIcon;
  href?: string;
  mono?: boolean;
}

/**
 * Facts in one dense row, not five stacked bands.
 *
 * The previous header spent four full-width sections and roughly 380px of
 * vertical space before the reader reached a single observation — on the
 * screen where the observations are the entire point. Same facts, one strip,
 * wrapping instead of stacking.
 *
 * Labels are sentence case rather than ALL CAPS: at 11px, caps are slower to
 * read and take more width to say the same thing.
 */
export function StatStrip({ stats, className }: { stats: Stat[]; className?: string }) {
  return (
    <dl className={cn('flex flex-wrap items-center gap-x-5 gap-y-2', className)}>
      {stats.map(({ label, value, icon: Icon, mono }) => (
        <div key={label} className="flex items-center gap-1.5">
          {Icon ? <Icon size={13} className="shrink-0 text-foreground-tertiary" /> : null}
          <dt className="text-xs text-foreground-tertiary">{label}</dt>
          <dd className={cn('text-xs font-bold tabular-nums', mono && 'font-mono font-normal')}>
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
