import { useState } from 'react';
import type { ReactNode } from 'react';
import { Box } from 'lucide-react';
import { Dialog } from './Dialog';
import { SpatialHero } from './SpatialHero';
import type { SpatialVariant } from './spatial/types';

/** Business pages open straight into their work; the spatial view remains optional. */
export function PageHeading({ title, description, scene, children }: {
  title: string; description: string; scene?: SpatialVariant; children?: ReactNode;
}) {
  const [viewOpen, setViewOpen] = useState(false);
  return <>
    <header className="page-heading work-heading">
      <div className="work-heading-copy">
        <div className="work-heading-title"><h1 className="page-title">{title}</h1>{scene && <button className="work-scene-trigger" onClick={() => setViewOpen(true)} aria-label={`查看${title}三维视图`} title="查看三维视图"><Box size={16}/><span>三维视图</span></button>}</div>
        <p className="page-description">{description}</p>
      </div>
      {children && <div className="work-heading-actions">{children}</div>}
    </header>
    {scene && viewOpen && <Dialog title={`${title} · 三维视图`} className="work-scene-dialog" onClose={() => setViewOpen(false)}><SpatialHero variant={scene} compact/></Dialog>}
  </>;
}
