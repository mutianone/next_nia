import { SpatialHero } from '../components/SpatialHero';
import { Check, Monitor, Moon, Sun, Palette, ArrowUpRight, Layers3, Zap } from 'lucide-react';
import { themes } from '../theme';
import type { ColorMode, ThemeId } from '../theme';

type Props = { theme: ThemeId; setTheme: (value: ThemeId) => void; mode: ColorMode; setMode: (value: ColorMode) => void; reducedMotion: boolean; setReducedMotion: (value: boolean) => void };
export function Appearance(props: Props) {
  return <div className="page-content appearance-page">
    <div className="page-heading"><div><h1 className="page-title">让工作空间，更合心意<span className="title-dot">.</span></h1><p className="page-description">五种气质，同一份专注。外观偏好会保存在当前浏览器。</p></div><div className="heading-icon"><Palette /></div></div>
    <SpatialHero variant="appearance" compact/>
    <div className="section-title"><h2>选择你的主题</h2><span className="muted">每套主题均支持明暗模式</span></div>
    <div className="theme-grid">{themes.map(t => <button key={t.id} className={`theme-card ${props.theme === t.id ? 'selected' : ''}`} onClick={() => props.setTheme(t.id)} aria-pressed={props.theme === t.id}>
      <div className="theme-miniature" style={{ background: t.background, '--preview-primary': t.color, '--preview-secondary': t.second } as React.CSSProperties}>
        <div className="mini-sidebar"><i /><i /><i /><i /><i /></div><div className="mini-main"><div className="mini-top"><i /><span /></div><div className="mini-hero"><i /><i /></div><div className="mini-cards"><i /><i /><i /></div><div className="mini-chart"><svg viewBox="0 0 200 45"><path d="M0 38 25 25 50 31 75 16 100 20 125 8 150 15 175 3 200 9" fill="none" stroke={t.color} strokeWidth="2.5" /></svg></div></div>
        <span className="theme-check">{props.theme === t.id ? <Check size={15} /> : <ArrowUpRight size={15} />}</span>
      </div><div className="theme-card-copy"><div><strong>{t.name}</strong><span>{t.tag}</span></div><p>{t.description}</p><div className="theme-swatches"><i style={{ background: t.color }} /><i style={{ background: t.second }} /><i style={{ background: t.background }} /></div></div>
    </button>)}</div>
    <div className="appearance-options"><section className="panel appearance-option"><div className="section-title"><div><h2><Layers3 size={18} /> 显示模式</h2><p className="muted">适应不同光线，让阅读更舒适</p></div></div><div className="mode-options">{([{ value: 'light', label: '白天', icon: Sun }, { value: 'dark', label: '黑夜', icon: Moon }, { value: 'system', label: '跟随系统', icon: Monitor }] as const).map(m => <button key={m.value} className={props.mode === m.value ? 'active' : ''} onClick={() => props.setMode(m.value)} aria-pressed={props.mode === m.value}><m.icon size={22} /><span>{m.label}</span>{props.mode === m.value && <Check size={14} />}</button>)}</div></section>
    <section className="panel appearance-option"><div className="section-title"><div><h2><Zap size={18} /> 动效偏好</h2><p className="muted">选择更适合自己的节奏</p></div></div><label className="motion-preference"><span><strong>减少动态效果</strong><small>暂停 3D 自动动效、卡片倾斜与背景流光，保留手动调整视角</small></span><input type="checkbox" role="switch" checked={props.reducedMotion} onChange={e => props.setReducedMotion(e.target.checked)} /></label><div className="appearance-note">系统的「减少动态效果」偏好也会自动生效。</div></section></div>
  </div>;
}
