import Link from 'next/link';
import { Card, EmptyState } from '@/components/ui/primitives';
import { cn } from '@/lib/utils';
import { cellRenderers, rowHref, type Row } from './cells';
import { DENSITY, type Column, type Density } from './columns';

interface Props {
  rows: Row[];
  columns: Column[];
  density: Density;
  isTrace: boolean;
  filtered: boolean;
}

export function TraceDataTable({ rows, columns, density, isTrace, filtered }: Props) {
  if (!rows.length) {
    return (
      <Card className="p-4">
        <EmptyState
          title="Nothing matches"
          hint={filtered
            ? 'Try widening the time range or clearing a filter.'
            : 'Traces appear here as soon as your application sends one.'}
        />
      </Card>
    );
  }

  const cell = cellRenderers(isTrace);

  return (
    <Card className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          {/* Header at `text-sm`, body at `text-xs`: density from the type
              scale, not from crushing the padding. */}
          <thead className="sticky top-12 z-10 bg-card">
            <tr className="border-b border-border text-left text-xs text-muted-foreground">
              {columns.map((column) => (
                <th key={column} className="h-10 whitespace-nowrap px-2 font-bold">{column}</th>
              ))}
            </tr>
          </thead>
          <tbody className="text-xs">
            {rows.map((row) => (
              <tr
                key={row.id}
                className={cn(
                  'border-b border-border/60 transition-colors last:border-0 hover:bg-muted',
                  DENSITY[density],
                )}
              >
                {columns.map((column, index) => (
                  <td key={column} className="max-w-[24rem] px-2 align-top">
                    {/* The link sits on the first cell rather than wrapping
                        the row: a <tr> is not a valid link parent, and
                        nesting one breaks keyboard traversal. */}
                    {index === 0 ? (
                      <Link
                        href={rowHref(row, isTrace)}
                        className="block py-2 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        {cell[column](row)}
                      </Link>
                    ) : (
                      <span className="block py-2">{cell[column](row)}</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
