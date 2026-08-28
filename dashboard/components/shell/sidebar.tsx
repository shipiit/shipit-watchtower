'use client';

import { PanelLeftClose, PanelLeftOpen, X } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Tooltip } from '@/components/ui/overlays';
import { cn } from '@/lib/utils';
import { NAV_GROUPS, isActive } from './navigation';

interface Props {
  collapsed: boolean;
  onToggle: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
}

export function Sidebar({ collapsed, onToggle, mobileOpen, onCloseMobile }: Props) {
  const pathname = usePathname();

  return (
    <aside
      className={cn(
        'fixed inset-y-0 left-0 z-40 flex flex-col border-r border-border bg-header',
        'transition-[width,transform] duration-200 ease-linear',
        collapsed ? 'w-[3.5rem]' : 'w-[14rem]',
        mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0',
      )}
    >
      <Brand collapsed={collapsed} onCloseMobile={onCloseMobile} />

      <nav className="flex-1 overflow-y-auto px-2 pb-2">
        {NAV_GROUPS.map((group) => (
          <section key={group.label} className={collapsed ? 'mt-2' : 'mt-4 first:mt-2'}>
            {collapsed ? (
              <div className="mx-2.5 mb-2 h-px bg-border" />
            ) : (
              <p className="px-2.5 pb-1 text-[10px] font-bold uppercase tracking-wider text-foreground-tertiary">
                {group.label}
              </p>
            )}
            <div className="flex flex-col gap-0.5">
              {group.links.map((link) => (
                <NavItem
                  key={link.href}
                  {...link}
                  collapsed={collapsed}
                  active={isActive(pathname, link.href)}
                  onNavigate={onCloseMobile}
                />
              ))}
            </div>
          </section>
        ))}
      </nav>

      <button
        type="button"
        onClick={onToggle}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        className={cn(
          'm-2 flex h-8 items-center gap-2.5 rounded-md px-2.5 text-xs',
          'text-foreground-tertiary transition-colors hover:bg-muted hover:text-foreground',
          'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring',
          collapsed && 'justify-center px-0',
        )}
      >
        {collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
        {collapsed ? null : (
          <>
            <span>Collapse</span>
            <kbd className="ml-auto rounded border border-border px-1 font-mono text-[10px]">⌘B</kbd>
          </>
        )}
      </button>
    </aside>
  );
}

function Brand({ collapsed, onCloseMobile }: { collapsed: boolean; onCloseMobile: () => void }) {
  return (
    <div className="flex h-12 shrink-0 items-center gap-2.5 border-b border-border px-3">
      <span className="grid size-6 shrink-0 place-items-center rounded-md bg-primary-accent text-[11px] font-bold text-white">
        S
      </span>
      {collapsed ? null : (
        <span className="flex min-w-0 flex-col leading-tight">
          <span className="truncate text-xs font-bold">Shipit Watcher</span>
          <span className="truncate text-[10px] text-foreground-tertiary">default project</span>
        </span>
      )}
      <button
        type="button"
        className="ml-auto text-muted-foreground lg:hidden"
        onClick={onCloseMobile}
        aria-label="Close navigation"
      >
        <X size={16} />
      </button>
    </div>
  );
}

function NavItem({ href, label, icon: Icon, collapsed, active, onNavigate }: {
  href: string;
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  collapsed: boolean;
  active: boolean;
  onNavigate: () => void;
}) {
  const link = (
    <Link
      href={href}
      onClick={onNavigate}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'group relative flex h-8 items-center gap-2.5 rounded-md px-2.5 text-xs transition-colors',
        'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring',
        active
          ? 'bg-muted font-bold text-foreground'
          : 'font-normal text-muted-foreground hover:bg-muted/60 hover:text-foreground',
        collapsed && 'justify-center px-0',
      )}
    >
      {/* A 2px accent rail rather than a filled block: it marks the row
          without competing with the content beside it. */}
      {active && !collapsed ? (
        <span className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-primary-accent" />
      ) : null}
      <Icon
        size={15}
        className={cn('shrink-0', active ? 'text-primary-accent' : 'text-foreground-tertiary')}
      />
      {collapsed ? null : <span className="truncate">{label}</span>}
    </Link>
  );

  // Collapsed, the icon is the only label there is.
  return collapsed ? <Tooltip content={label} side="right">{link}</Tooltip> : link;
}
