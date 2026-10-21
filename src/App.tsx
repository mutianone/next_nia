import { SpatialExperience } from './components/spatial/SpatialExperience';
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, ChevronDown, ChevronRight, CircleHelp, Command, Menu, Monitor, Moon, Palette, Search, Settings2, ShieldCheck, Sparkles, Sun, X } from 'lucide-react';
import { api } from './api';
import type { Health } from './api';
import { navigation, stages } from './navigation';
import type { PageId } from './navigation';
import { themes, useTheme } from './theme';
import { Dialog } from './components/Dialog';
import { MotionConfig, motion } from 'motion/react';
import { confirmAction } from './lib/confirmation';
const Dashboard = lazy(() => import('./pages/Dashboard').then(module => ({ default: module.Dashboard })));
const Appearance = lazy(() => import('./pages/Appearance').then(module => ({ default: module.Appearance })));
const Profiles = lazy(() => import('./pages/Profiles').then(module => ({ default: module.Profiles })));
const Analytics = lazy(() => import('./pages/Analytics').then(module => ({ default: module.Analytics })));
const Settings = lazy(() => import('./pages/Settings').then(module => ({ default: module.Settings })));
const People = lazy(() => import('./pages/People').then(module => ({ default: module.People })));
const Knowledge = lazy(() => import('./pages/Knowledge').then(module => ({ default: module.Knowledge })));
const Assistant = lazy(() => import('./pages/Assistant').then(module => ({ default: module.Assistant })));
const Alerts = lazy(() => import('./pages/Alerts').then(module => ({ default: module.Alerts })));
const Documents = lazy(() => import('./pages/Documents').then(module => ({ default: module.Documents })));
const Records = lazy(() => import('./pages/Records').then(module => ({ default: module.Records })));
import type { DocumentContext } from './document-types';

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
  const [documentContext, setDocumentContext] = useState<DocumentContext | null>(null);
  const dirtySources = useRef(new Set<string>());
  const navigationPending = useRef(false);
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
  const navigate = useCallback(async (next: PageId, context?: DocumentContext, replace = false) => {
    if (next === pageRef.current) { setSidebar(false); setSearchOpen(false); setThemeOpen(false); return; }
    if (navigationPending.current) return;
    navigationPending.current = true;
    try {
      if (dirtySources.current.size && !await confirmAction('当前页面还有未保存的内容。离开后，本次修改或尚未保存的录音将无法恢复。', { title: '离开前，要保留修改吗？', confirmLabel: '放弃修改并离开', cancelLabel: '继续编辑', tone: 'warning' })) return;
      if (context) setDocumentContext(context);
      dirtySources.current.clear();
      window.history[replace ? 'replaceState' : 'pushState'](null, '', `#/${next}`);
      pageRef.current = next; setPage(next); setSidebar(false); setSearchOpen(false); setThemeOpen(false); setSearch('');
      window.scrollTo(0, 0);
    } finally { navigationPending.current = false; }
  }, []);
  useEffect(() => {
    const hashChange = () => {
      const next = currentHash();
      if (next === pageRef.current) return;
      // Keep URL and editor aligned while an asynchronous decision is pending.
      window.history.replaceState(null, '', `#/${pageRef.current}`);
      void navigate(next, undefined, true);
    };
    const dirtyChange = (event: Event) => {
      const detail = (event as CustomEvent<{ source: string; dirty: boolean }>).detail;
      if (!detail?.source) return;
      if (detail.dirty) dirtySources.current.add(detail.source); else dirtySources.current.delete(detail.source);
    };
    const keys = (event: KeyboardEvent) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !document.querySelector('[role="dialog"], [role="alertdialog"]')) { event.preventDefault(); setSearchOpen(v => !v); } if (event.key === 'Escape') setSidebar(false); };
    window.addEventListener('hashchange', hashChange); window.addEventListener('settings-dirty-change', dirtyChange); document.addEventListener('keydown', keys);
    return () => { window.removeEventListener('hashchange', hashChange); window.removeEventListener('settings-dirty-change', dirtyChange); document.removeEventListener('keydown', keys); };
  }, [navigate]);
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(''), 3500); return () => window.clearTimeout(timer); }, [toast]);
  const selectedTheme = themes.find(t => t.id === theme.theme)!;
  const filtered = navigation.filter(n => `${n.label} ${n.en}`.toLowerCase().includes(search.toLowerCase()));
  return <SpatialExperience theme={theme.theme} reducedMotion={theme.reducedMotion} page={page}><MotionConfig reducedMotion={theme.reducedMotion ? 'always' : 'user'} transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}><div className="app-shell">
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); document.getElementById('main-content')?.focus(); }}>跳转到主要内容</a>
    {sidebar && <button className="sidebar-scrim" aria-label="关闭导航菜单" onClick={() => setSidebar(false)}/>}
    <aside className={`sidebar ${sidebar ? 'open' : ''}`} aria-label="主导航"><button className="brand" onClick={() => navigate('dashboard')} aria-label="境智首页"><span className="brand-mark"><svg viewBox="0 0 40 40" fill="none" aria-hidden="true"><path d="M8 10h24v6H14v12h18v6H8V10Z" fill="currentColor"/><path d="m23 4 13 13-13 13v-9h-8v-8h8V4Z" fill="#80cdb8"/></svg></span><span><strong>境智<span>NIA</span></strong><small>{systemName}</small></span></button>
      <div className="workspace-switch"><span className="workspace-icon"><ShieldCheck size={17}/></span><div><strong>{organization}</strong><small>本地工作空间</small></div><span className="workspace-dot"/></div>
      <nav className="nav-list">{navigation.map((item, i) => <div key={item.id}>{(i === 0 || item.group !== navigation[i - 1].group) && <div className="nav-group-label">{item.group}</div>}<button className={`nav-item ${page === item.id ? 'active' : ''}`} aria-current={page === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}><item.icon size={18} strokeWidth={1.7}/><span>{item.label}</span>{item.id === 'records' ? <span className="nav-mini-badge">AI</span> : item.stage > 4 ? <span className="nav-planned" title={`第${item.stage}阶段`}/> : null}{page === item.id && <ChevronRight size={14} className="nav-arrow"/>}</button></div>)}</nav>
      <div className="sidebar-bottom"><button className="phase-card" onClick={() => setHelpOpen(true)}><span className="phase-icon"><Sparkles size={17}/></span><div><strong>一起构建，更好的工作方式</strong><p>第五阶段 · 关联分析与交付</p><div className="phase-bars"><i/><i/><i/><i/><i/></div></div><ChevronRight size={14}/></button><div className="local-profile"><span className="profile-icon">境</span><div><strong>本地使用模式</strong><small>当前无需登录</small></div><button className="icon-button" aria-label="打开后台配置" onClick={() => navigate('settings')}><Settings2 size={17}/></button></div></div>
    </aside>
    <div className="main-shell"><header className="topbar"><div className="topbar-left"><button className="icon-button mobile-menu" aria-label="打开导航菜单" aria-expanded={sidebar} onClick={() => setSidebar(v => !v)}><Menu size={21}/></button><span className="breadcrumb-root">工作空间</span><ChevronRight size={13} className="breadcrumb-chevron"/><strong>{activeNav.label}</strong></div><div className="topbar-actions"><button className="search-trigger" onClick={() => setSearchOpen(true)} aria-label="搜索功能"><Search size={16}/><span>搜索功能…</span><kbd>⌘ K</kbd></button><span className={`environment-pill ${health ? '' : 'offline'}`}><i/>{health ? health.active_environment === 'test' ? '测试版' : '正式版' : '未连接'}</span><span className="topbar-divider"/><button className="icon-button" aria-label="切换主题" onClick={() => setThemeOpen(true)}><Palette size={18}/></button><button className="icon-button mode-toggle" aria-label={theme.mode === 'dark' ? '切换为白天模式' : '切换为黑夜模式'} onClick={() => theme.setMode(theme.mode === 'dark' ? 'light' : 'dark')}>{theme.mode === 'dark' ? <Moon size={18}/> : <Sun size={18}/>}</button><button className="icon-button help-trigger" aria-label="查看阶段说明" onClick={() => setHelpOpen(true)}><CircleHelp size={18}/></button></div></header>
      <motion.main id="main-content" tabIndex={-1} key={page} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}><Suspense fallback={<div className="page-content nia-page-loading" role="status"><span/><p>正在打开工作空间…</p></div>}>{page === 'dashboard' ? <Dashboard health={health} onNavigate={navigate} onHelp={() => setHelpOpen(true)}/> : page === 'settings' ? <div className="page-content settings-container"><Settings onSaved={() => { void refreshStatus(); setToast('配置已保存'); }}/></div>: page === 'alerts' ? <Alerts onNavigate={navigate} onCreateDocument={context => { void navigate('documents', context); }}/> : page === 'records' ? <Records onNavigate={navigate}/> : page === 'documents' ? <Documents initialContext={documentContext} onContextConsumed={() => setDocumentContext(null)}/> : page === 'people' ? <People/> : page === 'knowledge' ? <Knowledge onAsk={() => navigate('assistant')}/> : page === 'assistant' ? <Assistant onKnowledge={() => navigate('knowledge')} onSettings={() => navigate('settings')}/> : page === 'appearance' ? <Appearance {...theme}/> : page === 'profiles' ? <Profiles/> : <Analytics/>}</Suspense></motion.main>
      <nav className="mobile-bottom" aria-label="移动快捷导航">{navigation.filter(n => ['dashboard','knowledge','records','settings'].includes(n.id)).map(n => <button key={n.id} className={page === n.id ? 'active' : ''} onClick={() => navigate(n.id)}><n.icon size={20}/><span>{n.id === 'dashboard' ? '工作台' : n.id === 'knowledge' ? '知识' : n.id === 'records' ? '笔录' : '配置'}</span></button>)}<button onClick={() => setSidebar(true)}><Menu size={20}/><span>更多</span></button></nav>
    </div>
    {toast && <div className="toast" role="status"><ShieldCheck size={17}/>{toast}<button aria-label="关闭提示" onClick={() => setToast('')}><X size={14}/></button></div>}
    {searchOpen && <Dialog title="快速前往" onClose={() => setSearchOpen(false)} className="search-dialog"><div className="command-input"><Search size={20}/><input autoFocus value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索工作台、主题、后台配置…" aria-label="功能名称"/></div><div className="command-results">{filtered.map(n => <button key={n.id} onClick={() => navigate(n.id)}><span className="command-icon"><n.icon size={19}/></span><div><strong>{n.label}</strong><small>{n.en}</small></div><span className="command-state">{n.stage <= 5 ? '可用' : `第 ${n.stage} 阶段`}</span><ArrowRight size={15}/></button>)}{!filtered.length && <p className="empty-copy">没有找到对应功能，试试“配置”或“主题”。</p>}</div><div className="command-footer"><Command size={12}/> 快速导航<span>ESC 关闭</span></div></Dialog>}
    {themeOpen && <Dialog title="工作空间外观" onClose={() => setThemeOpen(false)} className="theme-dialog"><p className="muted dialog-description">当前：{selectedTheme.name}。选择后立即生效。</p><div className="theme-list">{themes.map(t => <button className={theme.theme === t.id ? 'selected' : ''} key={t.id} onClick={() => theme.setTheme(t.id)} aria-pressed={theme.theme === t.id}><span className="theme-color" style={{ background: `linear-gradient(135deg,${t.color},${t.second})` }}/><span><strong>{t.name}</strong><small>{t.tag}</small></span><span className="theme-radio"/></button>)}</div><div className="mode-options compact">{([{value:'light',name:'白天',icon:Sun},{value:'dark',name:'黑夜',icon:Moon},{value:'system',name:'跟随系统',icon:Monitor}] as const).map(m => <button className={theme.mode === m.value ? 'active' : ''} onClick={() => theme.setMode(m.value)} key={m.value} aria-pressed={theme.mode === m.value}><m.icon size={17}/>{m.name}</button>)}</div><button className="theme-all-button" onClick={() => navigate('appearance')}>浏览全部主题与动效设置 <ArrowRight size={15}/></button></Dialog>}
    {helpOpen && <Dialog title="循序构建，逐步交付" onClose={() => setHelpOpen(false)} className="progress-dialog"><div className="progress-intro"><span className="badge">当前版本 0.5.0</span><p>第五阶段提供带来源与核验状态的事实画像、人工关系图谱、业务统计、迁移预检及本地备份恢复。生产数据库、业务模板和目标手机仍需在目标环境验收。</p></div><div className="phase-timeline">{stages.map(s => <div className={`phase-row ${s.number === 5 ? 'current' : ''}`} key={s.number}><span className="phase-marker">0{s.number}</span><div><strong>{s.title}</strong><p>{s.detail}</p></div><small>{s.state}</small></div>)}</div><div className="progress-note"><ShieldCheck size={18}/><span>面向 macOS 和 Linux · 手机浏览器适配<br/>Android / HarmonyOS 安装包暂未启动</span></div><button className="btn btn-primary dialog-primary" onClick={() => setHelpOpen(false)}>了解，继续工作 <ChevronDown size={16}/></button></Dialog>}
  </div></MotionConfig></SpatialExperience>;
}
