import { createContext, useContext, useEffect } from 'react';
import type { ReactNode } from 'react';
import { useMediaQuery } from '../../hooks/useMediaQuery';
import { themes } from '../../theme';
import type { ThemeId } from '../../theme';

const SpatialContext = createContext({ reduced: false, accent: '#79c5bb', primary: '#245c78' });
export const useSpatialPreferences = () => useContext(SpatialContext);

export function SpatialExperience({ theme, reducedMotion, page, children }: { theme: ThemeId; reducedMotion: boolean; page: string; children: ReactNode }) {
  const systemReduced = useMediaQuery('(prefers-reduced-motion: reduce)');
  const finePointer = useMediaQuery('(hover: hover) and (pointer: fine)');
  const reduced = reducedMotion || systemReduced;
  const palette = themes.find(item => item.id === theme) || themes[0];
  useEffect(() => {
    if (reduced || !finePointer) return;
    let active: HTMLElement | null = null, frame = 0;
    const selector = '.dashboard-page .stat-card, .dashboard-page .quick-item, .theme-card';
    const reset = () => { if (frame) cancelAnimationFrame(frame); frame = 0; if (active) { active.removeAttribute('data-spatial-hover'); ['--card-x', '--card-y', '--spot-x', '--spot-y'].forEach(name => active?.style.removeProperty(name)); active = null; } };
    const move = (event: PointerEvent) => {
      const target = event.target instanceof Element ? event.target.closest<HTMLElement>(selector) : null;
      if (!target || event.pointerType === 'touch') { reset(); return; }
      if (active !== target) { reset(); active = target; }
      if (frame) cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        frame = 0; if (!target.isConnected) { reset(); return; }
        const box = target.getBoundingClientRect();
        const x = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
        const y = Math.max(0, Math.min(1, (event.clientY - box.top) / box.height));
        target.style.setProperty('--card-x', `${(y - .5) * -4}deg`); target.style.setProperty('--card-y', `${(x - .5) * 4}deg`);
        target.style.setProperty('--spot-x', `${x * 100}%`); target.style.setProperty('--spot-y', `${y * 100}%`); target.dataset.spatialHover = 'true';
      });
    };
    document.addEventListener('pointermove', move, { passive: true }); document.addEventListener('pointerleave', reset); window.addEventListener('blur', reset);
    return () => { reset(); document.removeEventListener('pointermove', move); document.removeEventListener('pointerleave', reset); window.removeEventListener('blur', reset); };
  }, [reduced, finePointer, page]);
  return <SpatialContext.Provider value={{ reduced, accent: palette.second, primary: palette.color }}>{children}</SpatialContext.Provider>;
}
