'use client';

import { useCallback, useRef, useState } from 'react';
import { cn } from '@/lib/utils';
import { usePreference } from '@/lib/table-preferences';

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

const parsePercent = (raw: string) => {
  const value = Number(raw.replaceAll('"', ''));
  return Number.isFinite(value) ? value : null;
};

/**
 * Two panes with a handle between them.
 *
 * The handle is a real `separator` with `aria-valuenow`, driven by pointer
 * capture *and* the arrow keys — a drag-only splitter is unusable without a
 * mouse, and this one sits between the thing you are reading and the detail
 * about it. Double-click resets, because after a few drags people want the
 * default back and hunting for it is worse than not having moved it.
 *
 * The width is remembered per `storageKey`: the split you want while reading
 * a trace tree is not the one you want while reading its payloads.
 */
export function SplitPane({
  left, right, storageKey, defaultPercent = 62, min = 30, max = 80, className,
}: {
  left: React.ReactNode;
  right: React.ReactNode;
  storageKey: string;
  defaultPercent?: number;
  min?: number;
  max?: number;
  className?: string;
}) {
  const [percent, setPercent] = usePreference<number>(storageKey, defaultPercent, parsePercent);
  const [dragging, setDragging] = useState(false);
  const container = useRef<HTMLDivElement>(null);

  const resize = useCallback((clientX: number) => {
    const bounds = container.current?.getBoundingClientRect();
    if (!bounds) return;
    setPercent(clamp(((clientX - bounds.left) / bounds.width) * 100, min, max));
  }, [max, min, setPercent]);

  return (
    <div
      ref={container}
      className={cn(
        'flex min-h-0 flex-col lg:flex-row',
        dragging && 'select-none [&_*]:cursor-col-resize',
        className,
      )}
      style={{ '--left': `${percent}%` } as React.CSSProperties}
    >
      <div className="min-h-0 min-w-0 flex-1 overflow-auto lg:flex-none lg:basis-[var(--left)]">
        {left}
      </div>

      <button
        type="button"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the detail pane"
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={Math.round(percent)}
        className={cn(
          'group relative hidden w-1.5 shrink-0 cursor-col-resize border-x border-border bg-card lg:block',
          'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring',
          dragging && 'bg-primary-accent/30',
        )}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          setDragging(true);
        }}
        onPointerMove={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) resize(event.clientX);
        }}
        onPointerUp={(event) => {
          event.currentTarget.releasePointerCapture(event.pointerId);
          setDragging(false);
        }}
        onLostPointerCapture={() => setDragging(false)}
        onDoubleClick={() => setPercent(defaultPercent)}
        onKeyDown={(event) => {
          if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
          event.preventDefault();
          setPercent(clamp(percent + (event.key === 'ArrowRight' ? 2 : -2), min, max));
        }}
      >
        {/* A 6px target is hard to hit, so the grip widens on hover without
            changing the layout. */}
        <span className="absolute inset-y-0 -left-1 -right-1 group-hover:bg-primary-accent/10" />
      </button>

      <div className="min-h-0 min-w-0 flex-1 overflow-auto">{right}</div>
    </div>
  );
}
