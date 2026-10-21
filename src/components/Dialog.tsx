import { useRef } from 'react';
import type { ReactNode } from 'react';
import { X } from 'lucide-react';
import { Dialog as DialogRoot, DialogContent, DialogTitle } from './ui/dialog';
import { Button } from './ui/button';

export function Dialog({ title, onClose, children, className = '' }: { title: string; onClose: () => void | Promise<void>; children: ReactNode; className?: string }) {
  const trigger = useRef(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  return <DialogRoot open onOpenChange={open => { if (!open) void onClose(); }}>
    <DialogContent className={`dialog ${className}`} showCloseButton={false} aria-describedby={undefined}
      onCloseAutoFocus={event => { event.preventDefault(); if (trigger.current?.isConnected) trigger.current.focus(); }}>
      <div className="dialog-heading"><div><span className="nia-dialog-eyebrow">境智 · 工作空间</span><DialogTitle>{title}</DialogTitle></div><Button variant="ghost" size="icon" aria-label="关闭弹窗" onClick={() => void onClose()}><X size={18}/></Button></div>
      {children}
    </DialogContent>
  </DialogRoot>;
}
