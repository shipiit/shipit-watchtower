import { RotateCcw, X } from 'lucide-react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { href } from './columns';

/** Filters that are part of the view, not of the query. */
const STRUCTURAL = new Set(['view', 'page', 'range', 'limit']);

export const appliedFilters = (active: Record<string, string>) =>
  Object.entries(active).filter(([key, value]) => value && !STRUCTURAL.has(key));

/**
 * Every active filter, visible and individually removable.
 *
 * A filter you cannot see is a filter you cannot undo — and "why is this
 * table empty" is almost always a forgotten one.
 */
export function FilterChips({ active }: { active: Record<string, string> }) {
  const applied = appliedFilters(active);
  if (!applied.length) return null;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {applied.map(([key, value]) => (
        <Link
          key={key}
          href={href(active, { [key]: '', page: 1 })}
          className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-0.5 text-xs hover:bg-muted"
        >
          <span className="text-foreground-tertiary">{key}</span>
          <span className="font-bold">{value}</span>
          <X size={11} />
        </Link>
      ))}
      <Button variant="ghost" size="sm" asChild>
        <Link href="/traces"><RotateCcw size={13} />Clear all</Link>
      </Button>
    </div>
  );
}
