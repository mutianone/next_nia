import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, ChevronDown, ChevronRight, CircleHelp, Command, Menu, Monitor, Moon, Palette, Search, Settings2, ShieldCheck, Sparkles, Sun, X } from 'lucide-react';
import { api } from './api';
import type { Health } from './api';
import { navigation, stages } from './navigation';
import type { PageId } from './navigation';
import { themes, useTheme } from './theme';
import { Dialog } from './components/Dialog';
import { Dashboard } from './pages/Dashboard';
import { Appearance } from './pages/Appearance';
import { Roadmap } from './pages/Roadmap';
import { Settings } from './pages/Settings';
import { People } from './pages/People';
import { Knowledge } from './pages/Knowledge';
import { Assistant } from './pages/Assistant';

const currentHash = (): PageId => { const id = window.location.hash.replace('#/', ''); return navigation.some(n => n.id === id) ? id as PageId : 'dashboard'; };
export function App() {
  const [page, setPage] = useState<PageId>(currentHash);
  const [sidebar, setSidebar] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [helpOpen, setHelpOpen] = useState(false);
  const [themeOpen, setThemeOpen] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [organization, setOrganization] = useState('出入境业务中心');
  const [systemName, setSystemName] = useState('境智 · 出入境业务平台');
  const [toast, setToast] = useState('');
  const dirty = useRef(false);
  const pageRef = useRef(page);
  pageRef.current = page;
  const theme = useTheme();
  const activeNav = navigation.find(n => n.id === page)!;
  const refreshStatus = useCallback(async () => {
    const results = await Promise.allSettled([api<Health>('/health'), api<{settings:{general:{organization:string;system_name:string}}}>('/settings')]);
    if (results[0].status === 'fulfilled') setHealth(results[0].value); else setHealth(null);
    if (results[1].status === 'fulfilled') { setOrganization(results[1].value.settings.general.organization); setSystemName(results[1].value.settings.general.system_name); }
  }, []);
  useEffect(() => { void refreshStatus(); const timer = window.setInterval(refreshStatus, 30000); return () => window.clearInterval(timer); }, [refreshStatus]);
  useEffect(() => { document.title = `${activeNav.label} · ${systemName}`; }, [activeNav.label, systemName]);
  useEffect(() => {
    const hashChange = () => { const next = currentHash(); if (next !== pageRef.current && dirty.current && !window.confirm('有内容尚未保存，确定离开并放弃修改吗？')) { window.history.replaceState(null, '', `#/${pageRef.current}`); return; } dirty.current = false; setPage(next); setSidebar(false); window.scrollTo(0, 0); };
    const dirtyChange = (event: Event) => { dirty.current = Boolean((event as CustomEvent<{dirty:boolean}>).detail?.dirty); };
    const keys = (event: KeyboardEvent) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setSearchOpen(v => !v); } if (event.key === 'Escape') setSidebar(false); };
    window.addEventListener('hashchange', hashChange); window.addEventListener('settings-dirty-change', dirtyChange); document.addEventListener('keydown', keys);
    return () => { window.removeEventListener('hashchange', hashChange); window.removeEventListener('settings-dirty-change', dirtyChange); document.removeEventListener('keydown', keys); };
  }, []);
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(''), 3500); return () => window.clearTimeout(timer); }, [toast]);
  const navigate = (next: PageId) => { if (next === page) { setSidebar(false); return; } window.location.hash = `/${next}`; setSearchOpen(false); setThemeOpen(false); setSearch(''); };
  const selectedTheme = themes.find(t => t.id === theme.theme)!;
  const filtered = navigation.filter(n => `${n.label} ${n.en}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="app-shell">
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); document.getElementById('main-content')?.focus(); }}>跳转到主要内容</a>
    {sidebar && <button className="sidebar-scrim" aria-label="关闭导航菜单" onClick={() => setSidebar(false)}/>}
    <aside className={`sidebar ${sidebar ? 'open' : ''}`} aria-label="主导航"><button className="brand" onClick={() => navigate('dashboard')} aria-label="境智首页"><span className="brand-mark"><svg viewBox="0 0 40 40" fill="none" aria-hidden="true"><path d="M8 10h24v6H14v12h18v6H8V10Z" fill="currentColor"/><path d="m23 4 13 13-13 13v-9h-8v-8h8V4Z" fill="#80cdb8"/></svg></span><span><strong>境智<span>NIA</span></strong><small>{systemName}</small></span></button>
      <div className="workspace-switch"><span className="workspace-icon"><ShieldCheck size={17}/></span><div><strong>{organization}</strong><small>本地工作空间</small></div><span className="workspace-dot"/></div>
      <nav className="nav-list">{navigation.map((item, i) => <div key={item.id}>{(i === 0 || item.group !== navigation[i - 1].group) && <div className="nav-group-label">{item.group}</div>}<button className={`nav-item ${page === item.id ? 'active' : ''}`} aria-current={page === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}><item.icon size={18} strokeWidth={1.7}/><span>{item.label}</span>{item.id === 'records' ? <span className="nav-mini-badge">AI</span> : item.stage > 2 ? <span className="nav-planned" title={`第${item.stage}阶段`}/> : null}{page === item.id && <ChevronRight size={14} className="nav-arrow"/>}</button></div>)}</nav>
      <div className="sidebar-bottom"><button className="phase-card" onClick={() => setHelpOpen(true)}><span className="phase-icon"><Sparkles size={17}/></span><div><strong>一起构建，更好的工作方式</strong><p>第二阶段 · 人员与法规知识</p><div className="phase-bars"><i/><i/><i/><i/><i/></div></div><ChevronRight size={14}/></button><div className="local-profile"><span className="profile-icon">境</span><div><strong>本地使用模式</strong><small>当前无需登录</small></div><button className="icon-button" aria-label="打开后台配置" onClick={() => navigate('settings')}><Settings2 size={17}/></button></div></div>
    </aside>
    <div className="main-shell"><header className="topbar"><div className="topbar-left"><button className="icon-button mobile-menu" aria-label="打开导航菜单" aria-expanded={sidebar} onClick={() => setSidebar(v => !v)}><Menu size={21}/></button><span className="breadcrumb-root">工作空间</span><ChevronRight size={13} className="breadcrumb-chevron"/><strong>{activeNav.label}</strong></div><div className="topbar-actions"><button className="search-trigger" onClick={() => setSearchOpen(true)} aria-label="搜索功能"><Search size={16}/><span>搜索功能…</span><kbd>⌘ K</kbd></button><span className={`environment-pill ${health ? '' : 'offline'}`}><i/>{health ? health.active_environment === 'test' ? '测试版' : '正式版' : '未连接'}</span><span className="topbar-divider"/><button className="icon-button" aria-label="切换主题" onClick={() => setThemeOpen(true)}><Palette size={18}/></button><button className="icon-button mode-toggle" aria-label={theme.mode === 'dark' ? '切换为白天模式' : '切换为黑夜模式'} onClick={() => theme.setMode(theme.mode === 'dark' ? 'light' : 'dark')}>{theme.mode === 'dark' ? <Moon size={18}/> : <Sun size={18}/>}</button><button className="icon-button help-trigger" aria-label="查看阶段说明" onClick={() => setHelpOpen(true)}><CircleHelp size={18}/></button></div></header>
      <main id="main-content" tabIndex={-1} key={page}>{page === 'dashboard' ? <Dashboard health={health} onNavigate={navigate} onHelp={() => setHelpOpen(true)}/> : page === 'settings' ? <div className="page-content settings-container"><Settings onSaved={() => { void refreshStatus(); setToast('配置已保存'); }}/></div>: page === 'people' ? <People/> : page === 'knowledge' ? <Knowledge onAsk={() => navigate('assistant')}/> : page === 'assistant' ? <Assistant onKnowledge={() => navigate('knowledge')} onSettings={() => navigate('settings')}/> : page === 'appearance' ? <Appearance {...theme}/> : <Roadmap page={page} onNavigate={navigate}/>}</main>
      <nav className="mobile-bottom" aria-label="移动快捷导航">{navigation.filter(n => ['dashboard','knowledge','records','settings'].includes(n.id)).map(n => <button key={n.id} className={page === n.id ? 'active' : ''} onClick={() => navigate(n.id)}><n.icon size={20}/><span>{n.id === 'dashboard' ? '工作台' : n.id === 'knowledge' ? '知识' : n.id === 'records' ? '笔录' : '配置'}</span></button>)}<button onClick={() => setSidebar(true)}><Menu size={20}/><span>更多</span></button></nav>
    </div>
    {toast && <div className="toast" role="status"><ShieldCheck size={17}/>{toast}<button aria-label="关闭提示" onClick={() => setToast('')}><X size={14}/></button></div>}
    {searchOpen && <Dialog title="快速前往" onClose={() => setSearchOpen(false)} className="search-dialog"><div className="command-input"><Search size={20}/><input autoFocus value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索工作台、主题、后台配置…" aria-label="功能名称"/></div><div className="command-results">{filtered.map(n => <button key={n.id} onClick={() => navigate(n.id)}><span className="command-icon"><n.icon size={19}/></span><div><strong>{n.label}</strong><small>{n.en}</small></div><span className="command-state">{n.stage <= 2 ? '可用' : `第 ${n.stage} 阶段`}</span><ArrowRight size={15}/></button>)}{!filtered.length && <p className="empty-copy">没有找到对应功能，试试“配置”或“主题”。</p>}</div><div className="command-footer"><Command size={12}/> 快速导航<span>ESC 关闭</span></div></Dialog>}
    {themeOpen && <Dialog title="工作空间外观" onClose={() => setThemeOpen(false)} className="theme-dialog"><p className="muted dialog-description">当前：{selectedTheme.name}。选择后立即生效。</p><div className="theme-list">{themes.map(t => <button className={theme.theme === t.id ? 'selected' : ''} key={t.id} onClick={() => theme.setTheme(t.id)} aria-pressed={theme.theme === t.id}><span className="theme-color" style={{ background: `linear-gradient(135deg,${t.color},${t.second})` }}/><span><strong>{t.name}</strong><small>{t.tag}</small></span><span className="theme-radio"/></button>)}</div><div className="mode-options compact">{([{value:'light',name:'白天',icon:Sun},{value:'dark',name:'黑夜',icon:Moon},{value:'system',name:'跟随系统',icon:Monitor}] as const).map(m => <button className={theme.mode === m.value ? 'active' : ''} onClick={() => theme.setMode(m.value)} key={m.value} aria-pressed={theme.mode === m.value}><m.icon size={17}/>{m.name}</button>)}</div><button className="theme-all-button" onClick={() => navigate('appearance')}>浏览全部主题与动效设置 <ArrowRight size={15}/></button></Dialog>}
    {helpOpen && <Dialog title="循序构建，逐步交付" onClose={() => setHelpOpen(false)} className="progress-dialog"><div className="progress-intro"><span className="badge">当前版本 0.2.0</span><p>第二阶段提供人员档案、停留记录、法规导入与版本审核、本地检索和引用问答。工作台统计来自当前环境数据库；预警、文书与笔录将在后续阶段接入。</p></div><div className="phase-timeline">{stages.map(s => <div className={`phase-row ${s.number === 2 ? 'current' : ''}`} key={s.number}><span className="phase-marker">0{s.number}</span><div><strong>{s.title}</strong><p>{s.detail}</p></div><small>{s.state}</small></div>)}</div><div className="progress-note"><ShieldCheck size={18}/><span>面向 macOS 和 Linux · 手机浏览器适配<br/>Android / HarmonyOS 安装包暂未启动</span></div><button className="btn btn-primary dialog-primary" onClick={() => setHelpOpen(false)}>了解，继续工作 <ChevronDown size={16}/></button></Dialog>}
  </div>;
}
