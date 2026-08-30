'use client';

import { useCallback, useMemo, useSyncExternalStore } from 'react';

const EVENT = 'watcher-table-prefs';

/**
 * A preference that lives in `localStorage`, read from where it actually is.
 *
 * Copying it into `useState` inside an effect is the usual shortcut, and it
 * is what made the saved columns load only when the drawer was opened —
 * written on every change, ignored on every page load. `useSyncExternalStore`
 * reads the real value, and the server snapshot matches the first render so
 * hydration cannot mismatch.
 */
export function usePreference<T>(
  key: string,
  fallback: T,
  parse: (raw: string) => T | null,
) {
  const subscribe = useCallback((notify: () => void) => {
    window.addEventListener(EVENT, notify);
    window.addEventListener('storage', notify);
    return () => {
      window.removeEventListener(EVENT, notify);
      window.removeEventListener('storage', notify);
    };
  }, []);

  const raw = useSyncExternalStore(subscribe, () => read(key), () => null);
  const value = useMemo(
    () => (raw ? parse(raw) ?? fallback : fallback),
    [raw, fallback, parse],
  );

  const set = useCallback((next: T) => {
    try {
      localStorage.setItem(key, JSON.stringify(next));
    } catch {
      // A preference that cannot be saved is still worth applying this session.
    }
    window.dispatchEvent(new Event(EVENT));
  }, [key]);

  return [value, set] as const;
}

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
