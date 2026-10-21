import { useEffect, useId } from 'react';

/** Each mounted editor reports independently; closing a nested dialog cannot clear its parent. */
export function usePageDirty(dirty: boolean) {
  const source = useId();
  useEffect(() => {
    const report = (value: boolean) => window.dispatchEvent(new CustomEvent('settings-dirty-change', { detail: { source, dirty: value } }));
    report(dirty);
    return () => { report(false); };
  }, [dirty, source]);
}
