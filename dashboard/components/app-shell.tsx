'use client';

import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import { Sidebar } from '@/components/shell/sidebar';
import { Topbar } from '@/components/shell/topbar';
import { TooltipProvider } from '@/components/ui/overlays';
import { useSidebarCollapsed } from '@/lib/use-sidebar';
import { cn } from '@/lib/utils';

/** Composition only — the sidebar, the top bar and the state each live apart. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, toggleCollapsed] = useSidebarCollapsed();

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'b' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        toggleCollapsed();
      }
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [toggleCollapsed]);

  // Sign-in renders without the shell: a sidebar full of links you cannot
  // follow yet is noise, and every one of them would bounce you back here.
  if (pathname === '/login') return <>{children}</>;

  return (
    <TooltipProvider>
      <div className="flex min-h-dvh bg-background text-foreground">
        {mobileOpen ? (
          <button
            type="button"
            aria-label="Close navigation"
            className="fixed inset-0 z-30 bg-black/40 lg:hidden"
            onClick={() => setMobileOpen(false)}
          />
        ) : null}

        <Sidebar
          collapsed={collapsed}
          onToggle={toggleCollapsed}
          mobileOpen={mobileOpen}
          onCloseMobile={() => setMobileOpen(false)}
        />

        <div
          className={cn(
            'flex min-w-0 flex-1 flex-col transition-[padding] duration-200 ease-linear',
            collapsed ? 'lg:pl-[3.5rem]' : 'lg:pl-[14rem]',
          )}
        >
          <Topbar onOpenMobile={() => setMobileOpen(true)} />
          <main className="min-w-0 flex-1 p-4 lg:p-6">{children}</main>
        </div>
      </div>
    </TooltipProvider>
  );
}
