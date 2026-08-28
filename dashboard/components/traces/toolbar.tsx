'use client';

import { Columns3, Download, Rows3, Search } from 'lucide-react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import {
  Card, DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel,
  DropdownMenuTrigger, Input, Tabs, TabsList, TabsTrigger,
} from '@/components/ui/primitives';
import { cn } from '@/lib/utils';
import {
  DENSITY_LABELS, RANGES, href, type Density,
} from './columns';

interface Props {
  view: string;
  active: Record<string, string>;
  density: Density;
  onDensity: (value: Density) => void;
  onToggleColumns: () => void;
}

export function TraceToolbar({ view, active, density, onDensity, onToggleColumns }: Props) {
  const range = active.range ?? '7d';
  const rangeLabel = RANGES.find(([value]) => value === range)?.[1] ?? 'Past 7 days';

  return (
    <Card className="flex flex-wrap items-center gap-2 p-2">
      <Tabs value={view}>
        <TabsList>
          <TabsTrigger value="traces" asChild>
            <Link href={href(active, { view: '', page: 1 })}>Traces</Link>
          </TabsTrigger>
          <TabsTrigger value="observations" asChild>
            <Link href={href(active, { view: 'observations', page: 1 })}>Observations</Link>
          </TabsTrigger>
        </TabsList>
      </Tabs>

      <SearchField active={active} />

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm">{rangeLabel}</Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          {RANGES.map(([value, label]) => (
            <DropdownMenuItem key={value} asChild>
              <Link href={href(active, { range: value, page: 1 })}>{label}</Link>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="icon" aria-label="Row height">
            <Rows3 size={14} />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuLabel>Row height</DropdownMenuLabel>
          {(Object.keys(DENSITY_LABELS) as Density[]).map((value) => (
            <DropdownMenuItem key={value} onSelect={() => onDensity(value)}>
              <span className={cn(density === value && 'font-bold text-foreground')}>
                {DENSITY_LABELS[value]}
              </span>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      <Button variant="outline" size="sm" onClick={onToggleColumns}>
        <Columns3 size={14} />Columns
      </Button>

      <Button variant="outline" size="sm" asChild>
        {/* Carries the whole active filter state, so the export matches what
            is on screen rather than silently exporting everything. */}
        <Link href={`/api/traces?limit=200&${new URLSearchParams(active).toString()}`}>
          <Download size={14} />JSON
        </Link>
      </Button>
    </Card>
  );
}

function SearchField({ active }: { active: Record<string, string> }) {
  return (
    <form action="/traces" className="relative min-w-[14rem] flex-1">
      {Object.entries(active)
        .filter(([key]) => key !== 'search' && key !== 'page')
        .map(([key, value]) => <input key={key} type="hidden" name={key} value={value} />)}
      <Search size={14} className="absolute left-2.5 top-2 text-foreground-tertiary" />
      <Input
        name="search"
        defaultValue={active.search ?? ''}
        placeholder="Search name, id, user, session, input or output…"
        className="pl-8"
      />
    </form>
  );
}
