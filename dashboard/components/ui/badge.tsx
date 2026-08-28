'use client';

import { cva, type VariantProps } from 'class-variance-authority';
import type * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * Status colours are always a **tinted fill with darker text of the same
 * hue**, never a bare coloured word and never a saturated fill with white
 * text. At 11px a coloured word on the page background is hard to read and
 * impossible to scan; the fill is what makes a column of statuses legible at
 * a glance.
 *
 * The pairs are defined once as tokens (`--light-red` / `--dark-red`), so a
 * call site cannot invent its own red.
 */
const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-md border border-transparent ' +
    'px-1.5 py-0.5 text-xs font-bold whitespace-nowrap',
  {
    variants: {
      variant: {
        default: 'bg-muted text-muted-foreground',
        outline: 'border-border text-muted-foreground',
        success: 'bg-light-green text-dark-green',
        warning: 'bg-light-yellow text-dark-yellow',
        error: 'bg-light-red text-dark-red',
        accent: 'bg-accent text-accent-foreground',
      },
    },
    defaultVariants: { variant: 'default' },
  },
);

export function Badge({
  className,
  variant,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

/**
 * Trace and observation status, mapped in exactly one place.
 *
 * The lookup is deliberately **total**: severity and status arrive as open
 * strings from OpenTelemetry and from user code, so an unrecognised value
 * must render as neutral rather than crash or vanish. `Object.hasOwn` rather
 * than `in`, so a status literally named `toString` cannot match the
 * prototype chain.
 */
const STATUS_VARIANTS: Record<string, VariantProps<typeof badgeVariants>['variant']> = {
  success: 'success',
  ok: 'success',
  default: 'default',
  debug: 'default',
  warning: 'warning',
  warn: 'warning',
  error: 'error',
  failed: 'error',
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const key = String(status ?? '').toLowerCase();
  const variant = Object.hasOwn(STATUS_VARIANTS, key) ? STATUS_VARIANTS[key] : 'default';
  return <Badge variant={variant} className={className}>{status || 'unknown'}</Badge>;
}
