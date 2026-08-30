'use client';

import * as Dialog from '@radix-ui/react-dialog';
import {
  ArrowDown, ArrowUp, Check, GripVertical, RotateCcw, X,
} from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { COLUMNS, DEFAULT_COLUMNS, type Column } from './columns';

interface Props {
  open: boolean;
  visible: Column[];
  onChange: (columns: Column[]) => void;
  onOpenChange: (open: boolean) => void;
}

const label = (column: Column) =>
  column.charAt(0).toUpperCase() + column.slice(1);

export function ColumnPicker({ open, visible, onChange, onOpenChange }: Props) {
  const [dragged, setDragged] = useState<Column | null>(null);
  const hidden = COLUMNS.filter((column) => !visible.includes(column));

  function toggle(column: Column) {
    if (visible.includes(column)) {
      // A table with no columns is neither useful nor recoverable without
      // reopening the drawer, so the last visible column stays enabled.
      if (visible.length > 1) onChange(visible.filter((name) => name !== column));
      return;
    }
    onChange([...visible, column]);
  }

  function move(source: Column, target: Column) {
    if (source === target) return;
    const sourceIndex = visible.indexOf(source);
    const originalTargetIndex = visible.indexOf(target);
    const next = visible.filter((column) => column !== source);
    const targetIndex = next.indexOf(target);
    // Dropping a row onto the next row should visibly move it after that row.
    // Once the source is removed, the target shifts left when moving down.
    const insertionIndex = sourceIndex < originalTargetIndex ? targetIndex + 1 : targetIndex;
    next.splice(insertionIndex, 0, source);
    onChange(next);
  }

  function nudge(column: Column, offset: -1 | 1) {
    const index = visible.indexOf(column);
    const target = index + offset;
    if (target < 0 || target >= visible.length) return;
    const next = [...visible];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30 backdrop-blur-[1px]" />
        <Dialog.Content
          id="trace-column-settings"
          className={cn(
            'fixed inset-y-0 right-0 z-50 flex w-full max-w-sm flex-col',
            'border-l border-border bg-background shadow-lift-md',
            'focus:outline-hidden',
          )}
        >
          <header className="flex items-start gap-3 border-b border-border p-4">
            <div className="min-w-0 flex-1">
              <Dialog.Title className="text-sm font-bold">Column visibility</Dialog.Title>
              <Dialog.Description className="mt-0.5 text-xs text-muted-foreground">
                Drag visible columns to change their table position.
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <Button variant="ghost" size="icon-sm" aria-label="Close column settings">
                <X size={15} />
              </Button>
            </Dialog.Close>
          </header>

          <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
            <p className="text-xs text-muted-foreground">
              <strong className="text-foreground">{visible.length}</strong> of {COLUMNS.length} shown
            </p>
            <Button variant="ghost" size="sm" onClick={() => onChange([...DEFAULT_COLUMNS])}>
              <RotateCcw size={13} />Restore defaults
            </Button>
          </div>

          <div className="flex-1 overflow-y-auto p-4">
            <section aria-labelledby="visible-columns-heading">
              <div className="mb-2 flex items-center justify-between">
                <h3 id="visible-columns-heading" className="text-[10px] font-bold uppercase tracking-wider text-foreground-tertiary">
                  Visible · drag to reorder
                </h3>
              </div>
              <ol className="space-y-1">
                {visible.map((column, index) => (
                  <li
                    key={column}
                    draggable
                    onDragStart={(event) => {
                      setDragged(column);
                      event.dataTransfer.effectAllowed = 'move';
                      event.dataTransfer.setData('text/plain', column);
                    }}
                    onDragOver={(event) => {
                      event.preventDefault();
                      event.dataTransfer.dropEffect = 'move';
                    }}
                    onDrop={(event) => {
                      event.preventDefault();
                      const source = dragged ?? event.dataTransfer.getData('text/plain') as Column;
                      if (COLUMNS.includes(source)) move(source, column);
                      setDragged(null);
                    }}
                    onDragEnd={() => setDragged(null)}
                    className={cn(
                      'group flex h-9 items-center gap-2 rounded-md border border-border bg-card px-2',
                      'transition-[border-color,opacity] hover:border-foreground-tertiary',
                      dragged === column && 'opacity-40',
                    )}
                  >
                    <GripVertical size={14} className="cursor-grab text-foreground-tertiary active:cursor-grabbing" />
                    <button
                      type="button"
                      role="checkbox"
                      aria-checked="true"
                      aria-label={`Hide ${label(column)} column`}
                      onClick={() => toggle(column)}
                      disabled={visible.length === 1}
                      className="grid size-4 place-items-center rounded border border-primary-accent bg-primary-accent text-white disabled:opacity-40"
                    >
                      <Check size={11} strokeWidth={3} />
                    </button>
                    <span className="min-w-0 flex-1 truncate text-xs font-bold">{label(column)}</span>
                    <span className="font-mono text-[10px] text-foreground-tertiary">{index + 1}</span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Move ${label(column)} earlier`}
                      disabled={index === 0}
                      onClick={() => nudge(column, -1)}
                    >
                      <ArrowUp size={12} />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Move ${label(column)} later`}
                      disabled={index === visible.length - 1}
                      onClick={() => nudge(column, 1)}
                    >
                      <ArrowDown size={12} />
                    </Button>
                  </li>
                ))}
              </ol>
            </section>

            {hidden.length ? (
              <section className="mt-5" aria-labelledby="hidden-columns-heading">
                <h3 id="hidden-columns-heading" className="mb-2 text-[10px] font-bold uppercase tracking-wider text-foreground-tertiary">
                  Hidden
                </h3>
                <ul className="space-y-1">
                  {hidden.map((column) => (
                    <li key={column}>
                      <button
                        type="button"
                        role="checkbox"
                        aria-checked="false"
                        onClick={() => toggle(column)}
                        className="flex h-9 w-full items-center gap-2 rounded-md border border-transparent px-2 text-left text-xs text-muted-foreground hover:border-border hover:bg-card hover:text-foreground"
                      >
                        <span className="size-4 rounded border border-border bg-background" />
                        <span>{label(column)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </div>

          <footer className="border-t border-border p-3 text-xs text-muted-foreground">
            Changes are saved automatically for this browser.
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
