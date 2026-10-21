import { useEffect, useSyncExternalStore } from 'react';
import { Archive, ArrowRight, Info, ShieldCheck, TriangleAlert } from 'lucide-react';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogTitle } from './ui/alert-dialog';
import { confirmAction, getConfirmation, settleConfirmation, subscribeConfirmation } from '../lib/confirmation';

export function ConfirmationHost() {
  const request = useSyncExternalStore(subscribeConfirmation, getConfirmation, () => null);
  useEffect(() => {
    // Native HTML validation still blocks invalid submission. Render its message
    // in our own dialog instead of the browser's validation bubble.
    const invalid = (event: Event) => {
      const field = event.target;
      if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement)) return;
      event.preventDefault();
      if (getConfirmation()) return;
      const name = field.getAttribute('aria-label') || field.labels?.[0]?.querySelector('span')?.textContent?.replace('*', '').trim() || '此项内容';
      const message = field.validity.valueMissing ? `请填写${name}后再继续。` : field.validity.rangeOverflow ? `${name}不能大于 ${(field instanceof HTMLInputElement ? field.max : '')}。` : field.validity.rangeUnderflow ? `${name}不能小于 ${(field instanceof HTMLInputElement ? field.min : '')}。` : `${name}的格式不正确，请检查后再继续。`;
      void confirmAction(message, { title: '还需要补充一点信息', confirmLabel: '返回填写', tone: 'info', alertOnly: true, returnFocus: field });
    };
    document.addEventListener('invalid', invalid, true);
    return () => { document.removeEventListener('invalid', invalid, true); const pending = getConfirmation(); if (pending) settleConfirmation(pending.id, false); };
  }, []);
  if (!request) return null;
  const tone = request.tone || 'warning';
  const Icon = request.alertOnly ? Info : request.confirmLabel?.includes('归档') ? Archive : tone === 'danger' ? TriangleAlert : ShieldCheck;
  const finish = (value: boolean) => settleConfirmation(request.id, value);
  return <AlertDialog open onOpenChange={open => { if (!open) finish(false); }}>
    <AlertDialogContent className={`nia-confirm tone-${tone}`} onCloseAutoFocus={event => { event.preventDefault(); const target = request.returnFocus || request.trigger; if (target?.isConnected) target.focus(); }}>
      <div className="nia-confirm-top"><span className="nia-confirm-icon"><Icon size={26} strokeWidth={1.6}/></span><span className="nia-dialog-eyebrow">{request.alertOnly ? '温馨提示' : '操作确认'}</span></div>
      <div className="nia-confirm-copy"><AlertDialogTitle>{request.title || '确认此操作？'}</AlertDialogTitle><AlertDialogDescription>{request.description}</AlertDialogDescription></div>
      <div className="nia-confirm-footer">
        {!request.alertOnly && <AlertDialogCancel onClick={() => finish(false)}>{request.cancelLabel || '取消'}</AlertDialogCancel>}
        <AlertDialogAction variant={tone === 'danger' ? 'destructive' : 'default'} onClick={() => finish(true)}>{request.confirmLabel || '确认继续'}<ArrowRight size={15}/></AlertDialogAction>
      </div>
    </AlertDialogContent>
  </AlertDialog>;
}
