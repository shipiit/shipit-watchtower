'use client';

import * as SeparatorPrimitive from '@radix-ui/react-separator';
import type * as React from 'react';
import { cn } from '@/lib/utils';

export function Separator({ className, orientation = 'horizontal', ...props }:
React.ComponentPropsWithoutRef<typeof SeparatorPrimitive.Root>) {
  return (
    <SeparatorPrimitive.Root
      decorative
      orientation={orientation}
      className={cn(
        'shrink-0 bg-border',
        orientation === 'horizontal' ? 'h-px w-full' : 'h-full w-px',
        className,
      )}
      {...props}
    />
  );
}
