'use client';

import { Command } from 'cmdk';
import {
  Activity, BarChart3, BookOpen, Boxes, BrainCircuit, CircleDollarSign,
  Database, Gauge, Hash, KeyRound, MessageSquareText, Moon, Search,
  ShieldCheck, Sun, Users,
} from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { cn } from '@/lib/utils';

/**
 * ⌘K.
 *
 * Two things make this worth more than a shortcut to the sidebar:
 *
 * 1. **It searches by synonym.** Every entry carries keywords, so "auth" and
 *    "token" both find API keys — you should not have to know that the page
 *    is called Settings.
 * 2. **Pasting an id offers to open it.** A 32-character hex string is a
 *    trace id and nothing else, so the palette detects one and offers to go
 *    straight there. Copying an id out of a log and finding the trace is the
 *    single most common thing anyone does with an observability tool, and
 *    everywhere else it takes four steps.
 */

interface Entry {
  group: string;
  label: string;
  href: string;
  icon: typeof Gauge;
  keywords?: string;
}

const ENTRIES: Entry[] = [
  { group: 'Observe', label: 'Overview', href: '/', icon: Gauge, keywords: 'home dashboard metrics' },
  { group: 'Observe', label: 'Traces', href: '/traces', icon: Activity, keywords: 'spans observations requests logs' },
  { group: 'Observe', label: 'Sessions', href: '/sessions', icon: MessageSquareText, keywords: 'threads conversations replay' },
  { group: 'Observe', label: 'Users', href: '/users', icon: Users, keywords: 'people customers spend' },
  { group: 'Observe', label: 'Models', href: '/models', icon: BrainCircuit, keywords: 'providers latency usage' },
  { group: 'Improve', label: 'Prompts', href: '/prompts', icon: BookOpen, keywords: 'registry versions labels templates' },
  { group: 'Improve', label: 'Datasets', href: '/datasets', icon: Database, keywords: 'examples experiments runs eval' },
  { group: 'Improve', label: 'Evaluations', href: '/evaluations', icon: BarChart3, keywords: 'scores judges quality annotation' },
  { group: 'Govern', label: 'Policies', href: '/policies', icon: ShieldCheck, keywords: 'guardrails blocked pii masking budget' },
  { group: 'Govern', label: 'Costs & budgets', href: '/costs', icon: CircleDollarSign, keywords: 'spend allocation cost centre money' },
  { group: 'Govern', label: 'Backends', href: '/backends', icon: Boxes, keywords: 'langfuse phoenix langsmith destinations' },
  { group: 'Settings', label: 'API keys', href: '/settings', icon: KeyRound, keywords: 'auth token secret ingest credentials rotate revoke' },
];

const TRACE_ID = /^[0-9a-f]{32}$/i;
const OBSERVATION_ID = /^[0-9a-f]{16,32}$/i;

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'k' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        setOpen((value) => !value);
      }
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, []);

  const go = useCallback((href: string) => {
    setOpen(false);
    setQuery('');
    router.push(href);
  }, [router]);

  const trimmed = query.trim();
  const looksLikeTraceId = TRACE_ID.test(trimmed);
  const looksLikeId = !looksLikeTraceId && OBSERVATION_ID.test(trimmed);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cn(
          'inline-flex h-8 items-center gap-2 rounded-md border border-border bg-card',
          'px-2.5 text-xs text-muted-foreground transition-colors hover:text-foreground',
          'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring',
        )}
      >
        <Search size={14} />
        <span className="hidden sm:inline">Search or jump to…</span>
        <kbd className="ml-2 hidden rounded border border-border px-1 font-mono text-[10px] sm:inline">
          ⌘K
        </kbd>
      </button>

      <Command.Dialog
        open={open}
        onOpenChange={setOpen}
        label="Command palette"
        shouldFilter={!looksLikeTraceId && !looksLikeId}
        className={cn(
          'fixed left-1/2 top-[18%] z-50 w-[min(560px,92vw)] -translate-x-1/2',
          'overflow-hidden rounded-lg border border-border bg-modal shadow-lg',
        )}
      >
        <div className="flex items-center gap-2 border-b border-border px-3">
          <Search size={15} className="text-foreground-tertiary" />
          <Command.Input
            value={query}
            onValueChange={setQuery}
            placeholder="Jump to a page, or paste a trace id…"
            className="h-11 w-full bg-transparent text-sm outline-hidden placeholder:text-foreground-tertiary"
          />
        </div>

        <Command.List className="max-h-[min(420px,60vh)] overflow-y-auto p-1.5">
          <Command.Empty className="px-3 py-6 text-center text-xs text-muted-foreground">
            Nothing matches that.
          </Command.Empty>

          {looksLikeTraceId ? (
            <Command.Group heading={<GroupLabel>Looks like an id</GroupLabel>}>
              <Item onSelect={() => go(`/traces/${trimmed}`)}>
                <Hash size={13} className="text-primary-accent" />
                Open trace <code className="font-mono text-foreground-tertiary">{trimmed.slice(0, 12)}…</code>
              </Item>
            </Command.Group>
          ) : null}

          {looksLikeId ? (
            <Command.Group heading={<GroupLabel>Looks like an id</GroupLabel>}>
              <Item onSelect={() => go(`/traces?search=${encodeURIComponent(trimmed)}`)}>
                <Search size={13} className="text-primary-accent" />
                Find traces containing <code className="font-mono text-foreground-tertiary">{trimmed}</code>
              </Item>
            </Command.Group>
          ) : null}

          {['Observe', 'Improve', 'Govern', 'Settings'].map((group) => (
            <Command.Group key={group} heading={<GroupLabel>{group}</GroupLabel>}>
              {ENTRIES.filter((entry) => entry.group === group).map((entry) => (
                <Item
                  key={entry.href + entry.label}
                  value={`${entry.label} ${entry.keywords ?? ''}`}
                  onSelect={() => go(entry.href)}
                >
                  <entry.icon size={13} className="text-muted-foreground" />
                  {entry.label}
                </Item>
              ))}
            </Command.Group>
          ))}

          <Command.Group heading={<GroupLabel>Appearance</GroupLabel>}>
            <Item value="theme dark light toggle appearance" onSelect={() => { toggleTheme(); setOpen(false); }}>
              <Sun size={13} className="text-muted-foreground dark:hidden" />
              <Moon size={13} className="hidden text-muted-foreground dark:block" />
              Toggle theme
            </Item>
          </Command.Group>
        </Command.List>
      </Command.Dialog>
    </>
  );
}

function GroupLabel({ children }: { children: React.ReactNode }) {
  return <span className="px-2 text-xs font-bold text-foreground-tertiary">{children}</span>;
}

function Item({ children, ...props }: React.ComponentProps<typeof Command.Item>) {
  return (
    <Command.Item
      {...props}
      className={cn(
        'flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs',
        'data-[selected=true]:bg-muted data-[selected=true]:text-foreground',
      )}
    >
      {children}
    </Command.Item>
  );
}

/** Shared with the header toggle, so both write the same key. */
export function toggleTheme() {
  const root = document.documentElement;
  const current = root.dataset.theme
    ?? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const next = current === 'dark' ? 'light' : 'dark';
  root.dataset.theme = next;
  root.style.colorScheme = next;
  try {
    localStorage.setItem('watcher-theme', next);
  } catch {
    // A browser with site data blocked still gets the toggle; it just will
    // not remember it. Losing the preference is not worth losing the toggle.
  }
}
