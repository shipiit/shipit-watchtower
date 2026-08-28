'use client';

import * as TabsPrimitive from '@radix-ui/react-tabs';
import type * as React from 'react';
import { cn } from '@/lib/utils';

export const Tabs = TabsPrimitive.Root;

export function TabsList({ className, ...props }: React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      className={cn('inline-flex items-center gap-0.5 rounded-md border border-border bg-card p-0.5', className)}
      {...props}
    />
  );
}

export function TabsTrigger({ className, ...props }: React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        'inline-flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-bold',
        'text-muted-foreground transition-colors hover:text-foreground',
        'data-[state=active]:bg-muted data-[state=active]:text-foreground',
        'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring',
        className,
      )}
      {...props}
    />
  );
}

export const TabsContent = TabsPrimitive.Content;
