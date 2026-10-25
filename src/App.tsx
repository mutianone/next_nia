import { useAuth } from './auth/AuthContext';
import { UserMenu } from './auth/UserMenu';
import { SpatialExperience } from './components/spatial/SpatialExperience';
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, ChevronRight, Command, Menu, Monitor, Moon, Palette, Search, ShieldCheck, Sun, X } from 'lucide-react';
import { api } from './api';
import { navigation } from './navigation';
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
  const { user } = useAuth();
  const allowedPage = (id: PageId) => (id !== 'settings' || user?.role === 'admin') && (id !== 'assistant' || user?.role !== 'viewer');
  const visibleNavigation = navigation.filter(n => allowedPage(n.id));
  const [page, setPage] = useState<PageId>(() => allowedPage(currentHash()) ? currentHash() : 'dashboard');
  const [sidebar, setSidebar] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [themeOpen, setThemeOpen] = useState(false);
  const [systemName, setSystemName] = useState('境智 · 出入境业务平台');
  const [toast, setToast] = useState('');
  const [alertContext, setAlertContext] = useState<string | null>(null);
  const [documentContext, setDocumentContext] = useState<DocumentContext | null>(null);
  const dirtySources = useRef(new Set<string>());
  const navigationPending = useRef(false);
  const pageRef = useRef(page);
  pageRef.current = page;
  const theme = useTheme();
  useEffect(() => { if (!allowedPage(currentHash())) window.history.replaceState(null, '', '#/dashboard'); }, [user?.role]);
  const activeNav = navigation.find(n => n.id === page)!;
  const refreshStatus = useCallback(async () => {
    try {
      const result = await api<{settings:{general:{organization:string;system_name:string}}}>('/settings');
      setSystemName(result.settings.general.system_name);
    } catch { /* Keep the last known workspace labels while the service is unavailable. */ }
  }, []);
  useEffect(() => { void refreshStatus(); const timer = window.setInterval(refreshStatus, 30000); return () => window.clearInterval(timer); }, [refreshStatus]);
  useEffect(() => { document.title = `${activeNav.label} · ${systemName}`; }, [activeNav.label, systemName]);
  const navigate = useCallback(async (next: PageId, context?: DocumentContext, replace = false) => {
    if (!allowedPage(next)) return;
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
  }, [user?.role]);
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
  const filtered = visibleNavigation.filter(n => `${n.label} ${n.en}`.toLowerCase().includes(search.toLowerCase()));
  return <SpatialExperience theme={theme.theme} reducedMotion={theme.reducedMotion} page={page}><MotionConfig reducedMotion={theme.reducedMotion ? 'always' : 'user'} transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}><div className="app-shell">
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); document.getElementById('main-content')?.focus(); }}>跳转到主要内容</a>
    {sidebar && <button className="sidebar-scrim" aria-label="关闭导航菜单" onClick={() => setSidebar(false)}/>}
    <aside className={`sidebar ${sidebar ? 'open' : ''}`} aria-label="主导航"><button className="brand" onClick={() => navigate('dashboard')} aria-label="境智首页"><span className="brand-mark"><img src="/favicon.svg?v=20261023-gate" alt="" aria-hidden="true" width="44" height="44"/></span><span><strong>境智<span>NIA</span></strong><small>{systemName}</small></span></button>
      <nav className="nav-list">{visibleNavigation.map((item, i) => <div key={item.id}>{(i === 0 || item.group !== visibleNavigation[i - 1].group) && <div className="nav-group-label">{item.group}</div>}<button className={`nav-item ${page === item.id ? 'active' : ''}`} aria-current={page === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}><item.icon size={18} strokeWidth={1.7}/><span>{item.label}</span>{page === item.id && <ChevronRight size={14} className="nav-arrow"/>}</button></div>)}</nav>
    </aside>
    <div className="main-shell"><header className="topbar"><div className="topbar-left"><button className="icon-button mobile-menu" aria-label="打开导航菜单" aria-expanded={sidebar} onClick={() => setSidebar(v => !v)}><Menu size={21}/></button><span className="breadcrumb-root">工作空间</span><ChevronRight size={13} className="breadcrumb-chevron"/><strong>{activeNav.label}</strong></div><div className="topbar-actions"><button className="search-trigger" onClick={() => setSearchOpen(true)} aria-label="搜索功能"><Search size={16}/><span>搜索功能…</span><kbd>⌘ K</kbd></button><span className="topbar-divider"/><button className="icon-button" aria-label="切换主题" onClick={() => setThemeOpen(true)}><Palette size={18}/></button><button className="icon-button mode-toggle" aria-label={theme.mode === 'dark' ? '切换为白天模式' : '切换为黑夜模式'} onClick={() => theme.setMode(theme.mode === 'dark' ? 'light' : 'dark')}>{theme.mode === 'dark' ? <Moon size={18}/> : <Sun size={18}/>}</button><UserMenu/></div></header>
      <motion.main id="main-content" tabIndex={-1} key={page} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: .14 }}><Suspense fallback={<div className="page-content nia-page-loading" role="status"><span/><p>正在打开工作空间…</p></div>}>{page === 'dashboard' ? <Dashboard onNavigate={navigate} onOpenAlert={id => { setAlertContext(id); void navigate('alerts'); }}/> : page === 'settings' ? <div className="page-content settings-container"><Settings onSaved={() => { void refreshStatus(); setToast('配置已保存'); }}/></div>: page === 'alerts' ? <Alerts initialId={alertContext} onContextConsumed={() => setAlertContext(null)} onNavigate={navigate} onCreateDocument={context => { void navigate('documents', context); }}/> : page === 'records' ? <Records onNavigate={navigate}/> : page === 'documents' ? <Documents initialContext={documentContext} onContextConsumed={() => setDocumentContext(null)}/> : page === 'people' ? <People/> : page === 'knowledge' ? <Knowledge onAsk={() => navigate('assistant')}/> : page === 'assistant' ? <Assistant onKnowledge={() => navigate('knowledge')} onSettings={() => navigate('settings')}/> : page === 'appearance' ? <Appearance {...theme}/> : page === 'profiles' ? <Profiles/> : <Analytics/>}</Suspense></motion.main>
      <nav className="mobile-bottom" aria-label="移动快捷导航">{visibleNavigation.filter(n => ['dashboard','knowledge','records','settings'].includes(n.id)).map(n => <button key={n.id} className={page === n.id ? 'active' : ''} onClick={() => navigate(n.id)}><n.icon size={20}/><span>{n.id === 'dashboard' ? '工作台' : n.id === 'knowledge' ? '知识' : n.id === 'records' ? '笔录' : '配置'}</span></button>)}<button onClick={() => setSidebar(true)}><Menu size={20}/><span>更多</span></button></nav>
    </div>
    {toast && <div className="toast" role="status"><ShieldCheck size={17}/>{toast}<button aria-label="关闭提示" onClick={() => setToast('')}><X size={14}/></button></div>}
    {searchOpen && <Dialog title="快速前往" onClose={() => setSearchOpen(false)} className="search-dialog"><div className="command-input"><Search size={20}/><input autoFocus value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索工作台、主题、系统配置…" aria-label="功能名称"/></div><div className="command-results">{filtered.map(n => <button key={n.id} onClick={() => navigate(n.id)}><span className="command-icon"><n.icon size={19}/></span><div><strong>{n.label}</strong><small>{n.en}</small></div><ArrowRight size={15}/></button>)}{!filtered.length && <p className="empty-copy">没有找到对应功能，试试“配置”或“主题”。</p>}</div><div className="command-footer"><Command size={12}/> 快速导航<span>ESC 关闭</span></div></Dialog>}
    {themeOpen && <Dialog title="工作空间外观" onClose={() => setThemeOpen(false)} className="theme-dialog"><p className="muted dialog-description">当前：{selectedTheme.name}。选择后立即生效。</p><div className="theme-list">{themes.map(t => <button className={theme.theme === t.id ? 'selected' : ''} key={t.id} onClick={() => theme.setTheme(t.id)} aria-pressed={theme.theme === t.id}><span className="theme-color" style={{ background: `linear-gradient(135deg,${t.color},${t.second})` }}/><span><strong>{t.name}</strong><small>{t.tag}</small></span><span className="theme-radio"/></button>)}</div><div className="mode-options compact">{([{value:'light',name:'白天',icon:Sun},{value:'dark',name:'黑夜',icon:Moon},{value:'system',name:'跟随系统',icon:Monitor}] as const).map(m => <button className={theme.mode === m.value ? 'active' : ''} onClick={() => theme.setMode(m.value)} key={m.value} aria-pressed={theme.mode === m.value}><m.icon size={17}/>{m.name}</button>)}</div><button className="theme-all-button" onClick={() => navigate('appearance')}>浏览全部主题与动效设置 <ArrowRight size={15}/></button></Dialog>}
  </div></MotionConfig></SpatialExperience>;
}
