import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react';

// Only transient view preferences live here. Never cache drafts, credentials or API responses.
const values = new Map<string, unknown>();
const positions = new Map<string, { top: number; row: string | null }>();
export function clearWorkspaceViews() { values.clear(); positions.clear(); }

export function useViewState<T>(key: string, initial: T | (() => T)): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => values.has(key) ? values.get(key) as T : typeof initial === 'function' ? (initial as () => T)() : initial);
  const update = useCallback<Dispatch<SetStateAction<T>>>(next => setValue(previous => {
    const resolved = typeof next === 'function' ? (next as (previous: T) => T)(previous) : next;
    values.set(key, resolved);
    return resolved;
  }), [key]);
  return [value, update];
}

/** Restore only once the list is visible and loaded; editor scroll never overwrites its list. */
export function useListPosition(key: string, active: boolean, ready: boolean) {
  const position = () => positions.get(key) ?? { top: 0, row: null };
  useEffect(() => {
    if (!active || !ready) return;
    const saved = position();
    let listening = false;
    const track = () => positions.set(key, { ...position(), top: window.scrollY });
    const frame = requestAnimationFrame(() => {
      window.scrollTo({ top: saved.top, behavior: 'instant' });
      if (saved.row) {
        const matches = [...document.querySelectorAll<HTMLElement>('[data-work-row]')].filter(el => el.dataset.workRow === saved.row);
        const row = matches.find(el => el.getClientRects().length > 0) ?? matches[0];
        const target = row?.matches('button') ? row : row?.querySelector<HTMLElement>('button, a');
        target?.focus({ preventScroll: true });
      }
      window.addEventListener('scroll', track, { passive: true });
      listening = true;
    });
    return () => { cancelAnimationFrame(frame); if (listening) window.removeEventListener('scroll', track); };
  }, [key, active, ready]);
  return {
    selectedRow: position().row,
    remember: (row: string) => positions.set(key, { top: window.scrollY, row }),
  };
}
