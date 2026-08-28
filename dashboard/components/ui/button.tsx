'use client';

import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import type * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * Sizes are shorter than the usual defaults because this is a dense
 * application: a 40px button next to a 28px table row makes the row look
 * broken. `font-bold` here is 600 — the theme redefines it, so there is no
 * third weight to drift into.
 */
const buttonVariants = cva(
  'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md ' +
    'text-xs font-bold transition-colors ' +
    'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring ' +
    'focus-visible:ring-offset-1 focus-visible:ring-offset-background ' +
    'disabled:pointer-events-none disabled:opacity-50 ' +
    "[&_svg]:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-primary/90',
        accent: 'bg-primary-accent text-white hover:bg-primary-accent/90',
        outline: 'border border-border bg-card hover:bg-muted hover:text-foreground',
        secondary: 'bg-muted text-foreground hover:bg-accent',
        ghost: 'hover:bg-muted hover:text-foreground',
        link: 'text-primary-accent underline-offset-4 hover:underline',
        destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive/90',
      },
      size: {
        default: 'h-8 px-3',
        sm: 'h-6 px-2 text-xs',
        lg: 'h-9 px-4 text-sm',
        icon: 'h-8 w-8',
        'icon-sm': 'h-6 w-6',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Component = asChild ? Slot : 'button';
  return (
    <Component className={cn(buttonVariants({ variant, size }), className)} {...props} />
  );
}

export { buttonVariants };
