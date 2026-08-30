'use client';

import {
  Bot, CircleDot, Fan, GitBranch, Layers3, Link2, ListTree, MoveHorizontal,
  Search, ShieldCheck, UserCheck, Wrench, type LucideIcon,
} from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * One icon and one colour per observation type, defined here and nowhere
 * else.
 *
 * The same map feeds the trace tree, the timeline, the table cells and the
 * filter facets, so a `tool` is the same orange wrench everywhere. Restating
 * the colours at call sites is how a retriever ends up teal in one view and
 * blue in another, and the reader stops trusting colour as a signal at all.
 *
 * Event types are open strings — the SDK's `EventType` is closed, but the
 * ingest API accepts whatever arrives — so `resolve` is total and falls back
 * to a neutral span rather than rendering nothing.
 */

interface ItemStyle {
  label: string;
  icon: LucideIcon;
  className: string;
}

const ITEM_TYPES: Record<string, ItemStyle> = {
  trace: { label: 'trace', icon: ListTree, className: 'text-dark-green' },
  generation: { label: 'generation', icon: Fan, className: 'text-fuchsia-500' },
  span: { label: 'span', icon: MoveHorizontal, className: 'text-sky-500' },
  event: { label: 'event', icon: CircleDot, className: 'text-emerald-500' },
  agent: { label: 'agent', icon: Bot, className: 'text-purple-500' },
  tool: { label: 'tool', icon: Wrench, className: 'text-orange-500' },
  tool_invocation: { label: 'tool', icon: Wrench, className: 'text-orange-500' },
  chain: { label: 'chain', icon: Link2, className: 'text-pink-500' },
  decision: { label: 'decision', icon: GitBranch, className: 'text-pink-500' },
  retrieval: { label: 'retriever', icon: Search, className: 'text-teal-500' },
  retriever: { label: 'retriever', icon: Search, className: 'text-teal-500' },
  embedding: { label: 'embedding', icon: Layers3, className: 'text-amber-500' },
  guardrail: { label: 'guardrail', icon: ShieldCheck, className: 'text-red-500' },
  policy: { label: 'guardrail', icon: ShieldCheck, className: 'text-red-500' },
  handoff: { label: 'handoff', icon: Bot, className: 'text-purple-500' },
  human_review: { label: 'human review', icon: UserCheck, className: 'text-blue-500' },
  validation: { label: 'evaluator', icon: CircleDot, className: 'text-indigo-500' },
};

const NEUTRAL: ItemStyle = {
  label: 'span',
  icon: MoveHorizontal,
  className: 'text-muted-foreground',
};

/** Total by construction — `Object.hasOwn`, never `in`. */
export function itemStyle(type: string | null | undefined): ItemStyle {
  const key = String(type ?? '').toLowerCase();
  return Object.hasOwn(ITEM_TYPES, key) ? ITEM_TYPES[key] : NEUTRAL;
}

export function ItemIcon({ type, size = 13, className }: {
  type: string | null | undefined;
  size?: number;
  className?: string;
}) {
  const style = itemStyle(type);
  const Icon = style.icon;
  return <Icon size={size} className={cn(style.className, className)} aria-hidden />;
}

export function ItemBadge({ type, showLabel = true, className }: {
  type: string | null | undefined;
  showLabel?: boolean;
  className?: string;
}) {
  const style = itemStyle(type);
  const Icon = style.icon;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-xs font-bold',
        className,
      )}
    >
      <Icon size={12} className={style.className} aria-hidden />
      {showLabel ? <span className="text-muted-foreground">{style.label}</span> : null}
    </span>
  );
}
