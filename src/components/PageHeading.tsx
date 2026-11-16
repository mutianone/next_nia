import type { ReactNode } from 'react';

export function PageHeading({ title, children }: {
  title: string; description: string; children?: ReactNode;
}) {
  return <header className="page-heading work-heading">
    <div className="work-heading-copy"><div className="work-heading-title"><h1 className="page-title">{title}</h1></div></div>
    {children && <div className="work-heading-actions">{children}</div>}
  </header>;
}
