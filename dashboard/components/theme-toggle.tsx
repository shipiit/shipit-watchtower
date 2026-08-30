'use client';

import { Moon, Sun } from 'lucide-react';
import { toggleTheme } from '@/components/command-palette';
import { Button } from '@/components/ui/button';

/** Light and dark. The icon shown is the theme you are *in*, not the one you get. */
export function ThemeToggle() {
  return (
    <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label="Toggle theme">
      <Sun size={15} className="dark:hidden" />
      <Moon size={15} className="hidden dark:block" />
    </Button>
  );
}
