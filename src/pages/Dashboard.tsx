import { TrendChart } from '../components/charts/TrendChart';
import { usePermissions } from '../auth/AuthContext';
import { formatDateTime } from '../lib/display';
import { SpatialHero } from '../components/SpatialHero';
import { useEffect, useState } from 'react';
import { ArrowRight, ArrowUpRight, AudioLines, BookOpen, CalendarDays, Check, ChevronRight, FileText, Layers3, Radio, Shield, Sparkles, UsersRound, WifiOff } from 'lucide-react';
import { api } from '../api';
import type { DashboardData } from '../api';
import type { PageId } from '../navigation';

export function Dashboard({ onNavigate, onOpenAlert }: { onNavigate: (page: PageId) => void; onOpenAlert?: (id: string) => void }) {
  const { write } = usePermissions();
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  async function refresh() { setLoading(true); setError(''); try { setData(await api<DashboardData>('/dashboard')); } catch (e) { setError(e instanceof Error ? e.message : '无法读取工作台'); } finally { setLoading(false); } }
  useEffect(() => { void refresh(); }, []);
  const stats = [
    { label: '人员档案', number: data?.stats.people, unit: '人', icon: UsersRound, caption: '查看登记册', color: 'blue', route: 'people' as PageId },
    { label: '待核查预警', number: data?.stats.pending_alerts, unit: '项', icon: Shield, caption: '查看核查队列', color: 'green', route: 'alerts' as PageId },
    { label: '已发布法规', number: data?.stats.knowledge_documents, unit: '份', icon: BookOpen, caption: '查看法规目录', color: 'orange', route: 'knowledge' as PageId },
    { label: '文书草稿', number: data?.stats.draft_records, unit: '份', icon: FileText, caption: '继续填写与核对', color: 'purple', route: 'documents' as PageId },
  ];
  const date = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric', weekday: 'long' }).formatToParts(new Date()).map(part => part.type === 'weekday' ? ` · ${part.value}` : part.value).join('');
  return <div className="page-content dashboard-page">
    {error && <div className="error-notice" role="alert"><WifiOff size={18}/><span>工作台暂时无法连接：{error}。请检查本地后端服务。</span><button onClick={refresh}>重试</button></div>}
    <h1 className="sr-only">总览工作台</h1>
    <SpatialHero variant="dashboard" corner={<div className="dashboard-date"><CalendarDays size={15}/><span>{date}</span></div>} title={<>待核查提醒 <strong className="work-hero-count">{data?.stats.pending_alerts ?? '—'}</strong><small className="work-hero-unit">项</small></>} description={data?.alerts_last_scan_at ? `上次扫描 ${formatDateTime(data.alerts_last_scan_at)} · 需人工核查` : '尚无扫描结果，请进入期限核查运行扫描。'} details={[]} action={<div className="work-hero-tasks"><button className="hero-button" onClick={() => onNavigate('alerts')}>前往核查<ArrowRight size={16}/></button><button className="hero-button work-hero-secondary" onClick={() => onNavigate('records')}>待整理笔录 <b>{data?.stats.transcript_drafts ?? '—'}</b><ChevronRight size={14}/></button></div>}/>

    <div className="stats-grid">{stats.map(s => <button className={`stat-card ${s.color}`} key={s.label} onClick={() => onNavigate(s.route)}><div className="stat-top"><span>{s.label}</span><span className="stat-icon"><s.icon size={19} strokeWidth={1.6}/></span></div><div className="stat-value">{s.number === undefined ? '—' : s.number.toLocaleString()}<small>{s.unit}</small></div><div className="stat-footer"><span>{s.caption}</span><ChevronRight size={14}/></div></button>)}</div>
    <div className="dashboard-middle"><section className="panel trend-panel"><div className="section-title"><div><h2>出入境动态 <span className="micro-badge">已登记</span></h2><p className="muted">人员档案中的日期记录 · 不代表外部业务系统总量</p></div></div><div className="chart-summary"><div><span className="chart-legend"/><span>入境</span><strong>{data?.trend.reduce((sum, d) => sum + d.entries, 0) ?? '—'}</strong><small>人次</small></div><div><span className="chart-legend secondary"/><span>出境</span><strong>{data?.trend.reduce((sum, d) => sum + d.exits, 0) ?? '—'}</strong><small>人次</small></div><span className="chart-period">最近 7 天 · 已登记记录</span></div><TrendChart data={data?.trend || []} compact barsOnly/></section>
    <section className="panel activity-panel"><div className="section-title"><h2>工作动态</h2><Radio size={16} className="muted"/></div><div className="activity-list">{data?.activities.map((a, i) => <div className="activity-item" key={a.id}><div className={`activity-marker marker-${i % 3}`}>{a.type === 'person' ? <UsersRound size={15}/> : a.type === 'document' ? <FileText size={15}/> : <Layers3 size={15}/>}</div><div><strong>{a.title}</strong><p>{a.description}</p><time>{a.time}</time></div></div>)}{!data?.activities.length && <p className="empty-copy">尚无业务动态</p>}</div></section></div>
    <div className="dashboard-bottom"><section className="panel alerts-panel"><div className="section-title"><div><h2>待核查事项 <span className="count-pill">{data?.alerts.length || 0}</span></h2><p className="muted">{data?.alerts_last_scan_at ? `上次扫描 ${formatDateTime(data.alerts_last_scan_at)}` : '尚未完成扫描，请进入预警中心运行扫描'}</p></div><button className="text-button" onClick={() => onNavigate('alerts')}>查看预警 <ArrowRight size={14}/></button></div><div className="alert-table-wrap"><table className="alert-table"><thead><tr><th>人员 / 档案编号</th><th>核查事项</th><th>剩余时间</th><th>状态</th><th aria-label="详情"/></tr></thead><tbody>{data?.alerts.map((a, i) => <tr key={a.id}><td><div className="person-cell"><span className={`avatar avatar-${i % 3}`}>{a.name.slice(-1)}</span><div><strong>{a.name}</strong><small>{a.code}</small></div></div></td><td>{a.category}</td><td><span className={`days-label ${a.days !== null && a.days <= 3 ? 'urgent' : ''}`}>{a.days === null ? '待核实' : a.days < 0 ? `已过 ${Math.abs(a.days)} 天` : a.days === 0 ? '今日到期' : `${a.days} 天`}</span></td><td><span className="status-label"><span/>{a.status}</span></td><td><button className="icon-button" aria-label={`查看${a.name}对应的期限预警`} onClick={() => onOpenAlert ? onOpenAlert(a.id) : onNavigate('alerts')}><ChevronRight size={16}/></button></td></tr>)}</tbody></table>{!data?.alerts.length && <div className="table-empty"><Check size={24}/><p>{loading ? '正在加载…' : '暂无待核查事项；以最近扫描结果为准'}</p></div>}</div></section>
    <section className="panel quick-panel"><div className="section-title"><h2>业务入口</h2></div><div className="quick-grid">{([{ title: '智能问答', text: '检索法规与引用', icon: Sparkles, page: 'assistant', color: 'blue', stage: 2 }, { title: '笔录整理', text: data?.stats.transcript_drafts ? `${data.stats.transcript_drafts} 份笔录待整理` : '录音、转写与校对', icon: AudioLines, page: 'records', color: 'purple', stage: 4 }, { title: '法规检索', text: '按适用日期查找条款', icon: BookOpen, page: 'knowledge', color: 'green', stage: 2 }, { title: '文书制作', text: '模板建稿与导出', icon: FileText, page: 'documents', color: 'orange', stage: 3 }] as const).filter(q => write || q.page !== 'assistant').map(q => <button key={q.page} className={`quick-item ${q.color}`} onClick={() => onNavigate(q.page)}><div><span className="quick-icon"><q.icon size={20}/></span><ArrowUpRight size={14}/></div><strong>{q.title}</strong><p>{q.text}</p></button>)}</div></section></div>

  </div>;
}
