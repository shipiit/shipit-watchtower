'use client';

import { ChevronRight } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { NAV_GROUPS } from './navigation';

const LABELS = new Map(
  NAV_GROUPS.flatMap((group) => group.links.map((link) => [link.href, link.label] as const)),
);

/**
 * Where you are, and one click back to where you came from.
 *
 * Ids are truncated to eight characters: a 32-character hex string in a
 * breadcrumb pushes everything else off the row and tells the reader nothing
 * the page title does not already say.
 */
export function Breadcrumbs() {
  const pathname = usePathname();
  if (pathname === '/') return null;

  const segments = pathname.split('/').filter(Boolean);
  const crumbs = segments.map((segment, index) => {
    const href = `/${segments.slice(0, index + 1).join('/')}`;
    const known = LABELS.get(href);
    const isId = /^[0-9a-f]{16,}$/i.test(segment);
    return {
      href,
      label: known ?? (isId ? `${segment.slice(0, 8)}…` : decodeURIComponent(segment)),
      last: index === segments.length - 1,
    };
  });

  return (
    <nav aria-label="Breadcrumb" className="hidden min-w-0 items-center gap-1 text-xs md:flex">
      {crumbs.map((crumb) => (
        <span key={crumb.href} className="flex min-w-0 items-center gap-1">
          {crumb.last ? (
            <span className="truncate font-bold text-foreground">{crumb.label}</span>
          ) : (
            <>
              <Link
                href={crumb.href}
                className="truncate text-muted-foreground transition-colors hover:text-foreground"
              >
                {crumb.label}
              </Link>
              <ChevronRight size={12} className="shrink-0 text-foreground-tertiary" />
            </>
          )}
        </span>
      ))}
    </nav>
  );
}
