export type ConfirmOptions = {
  title?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: 'warning' | 'danger' | 'info';
  alertOnly?: boolean;
  returnFocus?: HTMLElement;
};
export type Confirmation = ConfirmOptions & { id: number; description: string; trigger: HTMLElement | null };
let current: Confirmation | null = null;
let resolveCurrent: ((accepted: boolean) => void) | null = null;
let sequence = 0;
const listeners = new Set<() => void>();
export const getConfirmation = () => current;
export function subscribeConfirmation(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
function notify() { listeners.forEach(listener => listener()); }
export function confirmAction(description: string, options: ConfirmOptions = {}): Promise<boolean> {
  // Only one decision at a time. Repeated clicks cannot queue stale actions.
  if (current) return Promise.resolve(false);
  current = { id: ++sequence, description, ...options, trigger: document.activeElement instanceof HTMLElement ? document.activeElement : null };
  const result = new Promise<boolean>(resolve => { resolveCurrent = resolve; });
  notify();
  return result;
}
export function settleConfirmation(id: number, accepted: boolean) {
  if (!current || current.id !== id) return;
  const resolve = resolveCurrent;
  current = null; resolveCurrent = null;
  notify(); resolve?.(accepted);
}
