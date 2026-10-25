import { useEffect, useState } from 'react';

export const themes = [
  { id: 'government', name: '政务蓝', description: '秩序与专业，清晰的每一天', color: '#1e5bd6', second: '#4d98f5', background: '#f2f6fd', tag: '经典政企' },
  { id: 'jade', name: '青玉雅致', description: '温润青绿，让工作舒展有序', color: '#38735f', second: '#b3be91', background: '#edf1e9', tag: '自然雅致' },
  { id: 'space', name: '星舰指挥', description: '冰青光线，探索数字边界', color: '#147d9d', second: '#40c9e8', background: '#e8f4fa', tag: '科幻未来' },
  { id: 'aurora', name: '极光科技', description: '蓝紫流光，与灵感同行', color: '#7060b3', second: '#c1a1df', background: '#f0ecf8', tag: '渐变科技' },
  { id: 'graphite', name: '石墨极简', description: '专注信息本身的力量', color: '#515961', second: '#afb8b6', background: '#eeefef', tag: '纯粹简约' },
  { id: 'obsidian', name: '曜石金', description: '深石墨与香槟金，沉稳而有分寸', color: '#8c6a29', second: '#c7ad76', background: '#f5f3ee', tag: '典雅质感' },
] as const;
export type ThemeId = typeof themes[number]['id'];
export const fontSizes = [{ id: 'standard', label: '标准', percent: 100 }, { id: 'large', label: '较大', percent: 115 }, { id: 'larger', label: '大号', percent: 130 }, { id: 'largest', label: '特大', percent: 150 }] as const;
export type FontSize = typeof fontSizes[number]['id'];
export type ColorMode = 'light' | 'dark' | 'system';

function stored(key: string, fallback: string) { try { return localStorage.getItem(key) || fallback; } catch { return fallback; } }
export function useTheme() {
  const [theme, setTheme] = useState<ThemeId>(() => { const value = stored('nia-theme', 'government'); return themes.some(t => t.id === value) ? value as ThemeId : 'government'; });
  const [mode, setMode] = useState<ColorMode>(() => { const value = stored('nia-mode', 'light'); return ['light', 'dark', 'system'].includes(value) ? value as ColorMode : 'light'; });
  const [fontSize, setFontSize] = useState<FontSize>(() => { const value = stored('nia-font-size', 'standard'); return fontSizes.some(size => size.id === value) ? value as FontSize : 'standard'; });
  useEffect(() => { document.documentElement.dataset.fontSize = fontSize; try { localStorage.setItem('nia-font-size', fontSize); } catch { /* Session only. */ } }, [fontSize]);
  const [reducedMotion, setReducedMotion] = useState(() => stored('nia-reduced-motion', 'false') === 'true');
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const apply = () => { document.documentElement.dataset.theme = theme; document.documentElement.dataset.mode = mode === 'system' ? (media.matches ? 'dark' : 'light') : mode; };
    apply(); media.addEventListener('change', apply);
    try { localStorage.setItem('nia-theme', theme); localStorage.setItem('nia-mode', mode); } catch { /* Theme still works for this session. */ }
    return () => media.removeEventListener('change', apply);
  }, [theme, mode]);
  useEffect(() => { document.documentElement.dataset.reducedMotion = String(reducedMotion); try { localStorage.setItem('nia-reduced-motion', String(reducedMotion)); } catch { /* Session only. */ } }, [reducedMotion]);
  return { fontSize, setFontSize, theme, setTheme, mode, setMode, reducedMotion, setReducedMotion };
}
