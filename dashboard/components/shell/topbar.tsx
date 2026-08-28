'use client';

import { Menu, Moon, Sun } from 'lucide-react';
import Link from 'next/link';
import { CommandPalette, toggleTheme } from '@/components/command-palette';
import { Breadcrumbs } from '@/components/shell/breadcrumbs';
import { Button } from '@/components/ui/button';

export function Topbar({ onOpenMobile }: { onOpenMobile: () => void }) {
  return (
    <header className="sticky top-0 z-20 flex h-12 shrink-0 items-center gap-3 border-b border-border bg-background/80 px-3 backdrop-blur-md">
      <Button
        variant="ghost"
        size="icon"
        className="lg:hidden"
        onClick={onOpenMobile}
        aria-label="Open navigation"
      >
        <Menu size={17} />
      </Button>

      <Breadcrumbs />

      <div className="ml-auto flex items-center gap-1.5">
        <CommandPalette />
        <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label="Toggle theme">
          <Sun size={15} className="dark:hidden" />
          <Moon size={15} className="hidden dark:block" />
        </Button>
        <Button variant="ghost" size="sm" asChild>
          <Link href="/settings">Settings</Link>
        </Button>
      </div>
    </header>
  );
}
