import { useEffect, useId, useRef, type ReactNode } from 'react';
import { ArrowLeft } from 'lucide-react';

export function EditorWorkspace({ title, backLabel, onBack, children, className = '' }: { title: string; backLabel: string; onBack: () => void; children: ReactNode; className?: string }) {
  const heading = useRef<HTMLHeadingElement>(null);
  const id = useId();
  useEffect(() => { window.scrollTo({ top: 0, behavior: 'instant' }); heading.current?.focus({ preventScroll: true }); }, []);
  return <section className={`page-content editor-workspace ${className}`} aria-labelledby={id}>
    <header className="editor-workspace-heading"><button className="text-button" onClick={onBack}><ArrowLeft size={16}/>{backLabel}</button><h1 id={id} ref={heading} tabIndex={-1}>{title}</h1></header>
    {children}
  </section>;
}
