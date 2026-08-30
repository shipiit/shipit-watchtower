'use client';

import { ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { TraceDetail } from '@/lib/types';
import { JsonBlock } from './json-block';

/** Types that carry something a person would want to read as a conversation. */
const CONVERSATIONAL = new Set(['generation', 'tool_invocation', 'tool']);

export function MessagesView({ trace, onSelect }: {
  trace: TraceDetail;
  onSelect: (id: string) => void;
}) {
  const turns = trace.spans.filter((span) => CONVERSATIONAL.has(span.event_type));

  return (
    <div className="flex flex-col gap-3 p-3">
      <Turn label="Trace input"><JsonBlock value={trace.input} /></Turn>

      {turns.map((span) => (
        <Turn
          key={span.id}
          label={span.event_type === 'generation'
            ? `${span.provider || 'Model'} · ${span.model || 'generation'}`
            : span.name}
          assistant={span.event_type === 'generation'}
        >
          <JsonBlock value={span.output ?? span.input} />
          <button
            type="button"
            onClick={() => onSelect(span.id)}
            className="mt-1.5 inline-flex items-center gap-1 text-xs text-primary-accent hover:underline"
          >
            Inspect observation <ChevronRight size={12} />
          </button>
        </Turn>
      ))}

      <Turn label="Trace output" assistant><JsonBlock value={trace.output} /></Turn>
    </div>
  );
}

/**
 * A turn is a labelled block, not a chat bubble.
 *
 * Bubbles imply two speakers taking turns; a trace has a model, several
 * tools, and a retriever, and forcing that into left/right alignment loses
 * the distinction it is pretending to show.
 */
function Turn({ label, children, assistant = false }: {
  label: string;
  children: React.ReactNode;
  assistant?: boolean;
}) {
  return (
    <article className={cn('rounded-md border border-border p-2.5', assistant && 'bg-muted/40')}>
      <span className="mb-1.5 block text-xs font-bold text-foreground-tertiary">{label}</span>
      {children}
    </article>
  );
}
