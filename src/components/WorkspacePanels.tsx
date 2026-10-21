import type { ReactNode } from 'react';
import { useMediaQuery } from '../hooks/useMediaQuery';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from './ui/resizable';

export function WorkspacePanels({ children, aside }: { children: ReactNode; aside: ReactNode }) {
  const wide = useMediaQuery('(min-width: 1100px)');
  if (!wide) return <div className="nia-workspace">{children}{aside}</div>;
  return <><p className="nia-workspace-hint">拖动分隔线调整阅读空间 · 支持方向键</p><ResizablePanelGroup orientation="horizontal" className="nia-workspace">
    <ResizablePanel defaultSize="72%" minSize="55%">{children}</ResizablePanel>
    <ResizableHandle withHandle aria-label="调整笔录工作区与记录侧栏的宽度"/>
    <ResizablePanel defaultSize="28%" minSize="23%" maxSize="40%">{aside}</ResizablePanel>
  </ResizablePanelGroup></>;
}
