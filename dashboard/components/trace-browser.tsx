'use client';

import { useState } from 'react';
import { ColumnPicker } from '@/components/traces/column-picker';
import { TraceDataTable } from '@/components/traces/data-table';
import { FilterChips, appliedFilters } from '@/components/traces/filter-chips';
import { TracePagination } from '@/components/traces/pagination';
import { TraceToolbar } from '@/components/traces/toolbar';
import {
  COLUMN_KEY, DEFAULT_COLUMNS, DENSITY_KEY, parseColumns, parseDensity,
  type Column, type Density,
} from '@/components/traces/columns';
import { usePreference } from '@/lib/table-preferences';
import type { ObservationRow, TraceRow } from '@/lib/types';

interface Props {
  traces: TraceRow[];
  observations: ObservationRow[];
  total: number;
  page: number;
  limit: number;
  view: string;
  active: Record<string, string>;
}

/** Composition only — every part of this screen lives in its own file. */
export function TraceBrowser({ traces, observations, total, page, limit, view, active }: Props) {
  const [showColumns, setShowColumns] = useState(false);
  const [columns, setColumns] = usePreference<Column[]>(COLUMN_KEY, DEFAULT_COLUMNS, parseColumns);
  const [density, setDensity] = usePreference<Density>(DENSITY_KEY, 's', parseDensity);

  const isTrace = view === 'traces';
  const rows = isTrace ? traces : observations;
  const pages = Math.max(1, Math.ceil(total / limit));

  return (
    <div className="flex flex-col gap-3">
      <TraceToolbar
        view={view}
        active={active}
        density={density}
        onDensity={setDensity}
        onToggleColumns={() => setShowColumns((open) => !open)}
      />
      <FilterChips active={active} />
      {showColumns ? <ColumnPicker visible={columns} onChange={setColumns} /> : null}
      <TraceDataTable
        rows={rows}
        columns={columns}
        density={density}
        isTrace={isTrace}
        filtered={appliedFilters(active).length > 0}
      />
      <TracePagination
        total={total}
        page={page}
        pages={pages}
        label={isTrace ? 'traces' : 'observations'}
        active={active}
      />
    </div>
  );
}
