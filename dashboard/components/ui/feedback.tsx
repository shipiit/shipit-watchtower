'use client';

import type * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * Skeletons for content, spinners only inside buttons.
 *
 * A spinner says "something is happening"; a skeleton says "this shape is
 * about to be filled", which stops the layout jumping when it is.
 */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('animate-pulse rounded-md bg-muted', className)} {...props} />;
}

export function EmptyState({ title, hint, icon }: {
  title: string;
  hint?: React.ReactNode;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border px-6 py-12 text-center">
      {icon ? <div className="text-foreground-tertiary">{icon}</div> : null}
      <p className="text-sm font-bold text-foreground">{title}</p>
      {/* An empty project should teach, not just report emptiness. */}
      {hint ? <p className="max-w-md text-xs leading-relaxed text-muted-foreground">{hint}</p> : null}
    </div>
  );
}
