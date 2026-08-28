'use client';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/primitives';
import { cn } from '@/lib/utils';
import { COLUMNS, DEFAULT_COLUMNS, type Column } from './columns';

interface Props {
  visible: Column[];
  onChange: (columns: Column[]) => void;
}

export function ColumnPicker({ visible, onChange }: Props) {
  const toggle = (column: Column) => onChange(
    visible.includes(column)
      ? visible.filter((name) => name !== column)
      : [...visible, column],
  );

  return (
    <Card className="p-3">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-bold">Visible columns</p>
        <Button variant="ghost" size="sm" onClick={() => onChange(DEFAULT_COLUMNS)}>
          Restore defaults
        </Button>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {COLUMNS.map((column) => (
          <button
            key={column}
            type="button"
            aria-pressed={visible.includes(column)}
            onClick={() => toggle(column)}
            className={cn(
              'rounded-md border px-2 py-0.5 text-xs transition-colors',
              visible.includes(column)
                ? 'border-transparent bg-muted font-bold text-foreground'
                : 'border-border text-muted-foreground hover:text-foreground',
            )}
          >
            {column}
          </button>
        ))}
      </div>
    </Card>
  );
}
