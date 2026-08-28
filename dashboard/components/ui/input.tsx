'use client';

import type * as React from 'react';
import { cn } from '@/lib/utils';

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        'h-8 w-full rounded-md border border-input bg-card px-2.5 text-xs',
        'placeholder:text-foreground-tertiary',
        'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  );
}
