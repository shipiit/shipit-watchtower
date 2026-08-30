'use client';

import { useCallback, useSyncExternalStore } from 'react';

const KEY = 'watcher-sidebar';
const EVENT = 'watcher-sidebar-change';

/**
 * Whether the sidebar is collapsed, read from where it actually lives.
 *
 * `localStorage` is external state, so it is read through
 * `useSyncExternalStore` rather than copied into `useState` inside an effect.
 * The server snapshot is `false`, matching the first render, so hydration
 * cannot mismatch — and a browser with site data blocked simply starts
 * expanded instead of throwing.
 */
export function useSidebarCollapsed(): [boolean, () => void] {
  const subscribe = useCallback((notify: () => void) => {
    window.addEventListener(EVENT, notify);
    window.addEventListener('storage', notify);
    return () => {
      window.removeEventListener(EVENT, notify);
      window.removeEventListener('storage', notify);
    };
  }, []);

  const collapsed = useSyncExternalStore(subscribe, read, () => false);

  const toggle = useCallback(() => {
    try {
      localStorage.setItem(KEY, collapsed ? 'expanded' : 'collapsed');
    } catch {
      // Not worth failing the toggle over; it just will not be remembered.
    }
    window.dispatchEvent(new Event(EVENT));
  }, [collapsed]);

  return [collapsed, toggle];
}

function read(): boolean {
  try {
    return localStorage.getItem(KEY) === 'collapsed';
  } catch {
    return false;
  }
}
