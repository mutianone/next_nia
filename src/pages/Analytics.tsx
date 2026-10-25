import { TrendChart } from '../components/charts/TrendChart';
import { DistributionChart } from '../components/charts/DistributionChart';
import { Access } from '../auth/AuthContext';
import { formatDateTime } from '../lib/display';
import { useEffect, useState } from 'react';
import { ArrowDownToLine, ArrowDownRight, ArrowUpRight, CalendarDays, ChartNoAxesCombined, Expand, Info, RefreshCw, UsersRound } from 'lucide-react';
import { api } from '../api';
import { SpatialHero } from '../components/SpatialHero';
import { Dialog } from '../components/Dialog';

type Group = { label: string; value: number };
export type Report = { start: string; end: string; include_archived: boolean; generated_at: string; last_scan_at: string | null; stock: Record<string, number>; period: Record<string, number>; trend: { date: string; entries: number; exits: number }[]; nationalities: Group[]; purposes: Group[]; alert_status: Group[]; document_status: Group[]; record_status: Group[]; definitions: string[] };
const statusLabels: Record<string, string> = { open:'待核查',in_progress:'核查中',closed:'已关闭',draft:'草稿',reviewed:'已核对',archived:'已归档' };
export async function downloadFile(path: string, filename: string, init?: RequestInit) {
  const response = await fetch('/api'+path, init);
  if (!response.ok) { const data = await response.json().catch(()=>null); throw new Error(data?.detail || '导出失败，请重试。'); }
  const blob = await response.blob(), url = URL.createObjectURL(blob), anchor = document.createElement('a'); anchor.href=url; anchor.download=filename; anchor.click(); setTimeout(()=>URL.revokeObjectURL(url), 1000);
}
function ReportView({ data }: { data: Report }) {
  return <>
    <div className="p5-report-metrics">{[{key:'people',label:'当前人员档案',icon:UsersRound,value:data.stock.people,sub:`已核验 ${data.stock.verified} 人`},{key:'entries',label:'区间登记入境',icon:ArrowDownRight,value:data.period.entries,sub:'按实际入境日期'},{key:'exits',label:'区间登记离境',icon:ArrowUpRight,value:data.period.exits,sub:'按实际离境日期'},{key:'pending_alerts',label:'当前待核查事项',icon:Info,value:data.stock.pending_alerts,sub:'最近扫描结果'}].map(item=><article className="p5-metric" key={item.key}><header><span>{item.label}</span><item.icon size={19}/></header><strong>{item.value.toLocaleString()}</strong><small>{item.sub}</small></article>)}</div>
    <section className="panel p5-trend"><div className="section-title"><div><h2>出入境登记趋势</h2><p className="p5-caption">{data.start} — {data.end} · 人次</p></div></div><TrendChart data={data.trend.map(row => ({ ...row, label: row.date }))} lineOnly/></section>
    <div className="p5-chart-grid"><DistributionChart title="国籍登记分布" data={data.nationalities}/><DistributionChart title="来华事由分布" data={data.purposes} initial="rose"/></div>
    <div className="p5-status-grid">{([{key:'alert_status',label:'区间新建预警'},{key:'document_status',label:'区间新建文书'},{key:'record_status',label:'区间新建笔录'}] as const).map(item => <DistributionChart key={item.key} title={item.label} data={data[item.key].map(row => ({...row,label:statusLabels[row.label]||row.label}))} caption="区间新建记录的当前状态"/>)}</div>
    <section className="panel p5-definitions"><div className="section-title"><h2><Info size={16}/>统计口径</h2><span className="badge">可追溯</span></div><ul>{data.definitions.map(text=><li key={text}>{text}</li>)}</ul><p>报告生成：{formatDateTime(data.generated_at)} · 上次扫描：{data.last_scan_at?formatDateTime(data.last_scan_at):'尚未扫描'}</p></section>
  </>;
}
export function Analytics() {
  const [draft,setDraft]=useState({start:'',end:'',include_archived:false}), [filter,setFilter]=useState(draft), [data,setData]=useState<Report|null>(null), [error,setError]=useState(''), [busy,setBusy]=useState(false), [reload,setReload]=useState(0), [full,setFull]=useState(false), [exporting,setExporting]=useState(false);
  const params=new URLSearchParams({...(filter.start?{start:filter.start}:{}),...(filter.end?{end:filter.end}:{}),include_archived:String(filter.include_archived)}).toString();
  useEffect(()=>{const controller=new AbortController();setBusy(true);setError('');setData(null);api<Report>('/analytics?'+params,{signal:controller.signal}).then(value=>{setData(value);setDraft({start:value.start,end:value.end,include_archived:value.include_archived});}).catch(e=>{if(!controller.signal.aborted)setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setBusy(false);});return()=>controller.abort();},[params,reload]);
  async function download(){setExporting(true);setError('');try{await downloadFile('/analytics/export?'+params,'境智业务统计.csv');}catch(e){setError(e instanceof Error?e.message:'导出失败');}finally{setExporting(false);}}
  return <div className="page-content p5-page"><div className="page-heading"><div><h1 className="page-title">业务统计</h1></div><div className="p5-actions"><button className="btn btn-secondary" disabled={!data||busy} onClick={()=>setFull(true)}><Expand size={15}/>大屏展示</button><Access permission="write"><button className="btn btn-primary" disabled={!data||busy||exporting} onClick={()=>void download()}><ArrowDownToLine size={15}/>{exporting?'导出中…':'导出报表'}</button></Access></div></div>
    <form className="panel p5-filters" noValidate onSubmit={e=>{e.preventDefault();if(!draft.start||!draft.end){setError("请选择完整的统计日期区间。");return;}setFilter({...draft});setReload(v=>v+1);}}><CalendarDays size={18}/><label>开始日期<input className="input" aria-label="统计开始日期" type="date" value={draft.start} required onChange={e=>setDraft({...draft,start:e.target.value})}/></label><label>结束日期<input className="input" aria-label="统计结束日期" type="date" value={draft.end} required onChange={e=>setDraft({...draft,end:e.target.value})}/></label><label className="p5-check"><input type="checkbox" checked={draft.include_archived} onChange={e=>setDraft({...draft,include_archived:e.target.checked})}/>包含归档资料</label><button className="btn btn-secondary" disabled={busy}><RefreshCw size={14}/>{busy?'读取中…':'更新统计'}</button></form>
    {error&&<div className="p5-notice error" role="alert">{error}<button className="p5-text-button" onClick={()=>setReload(v=>v+1)}>重试</button></div>}{busy&&<div className="panel p5-empty"><ChartNoAxesCombined size={30}/><p>正在汇总当前数据库…</p></div>}{data&&<ReportView data={data}/>}{full&&data&&<Dialog title={`业务统计 · ${data.start} 至 ${data.end}`} onClose={()=>setFull(false)} className="p5-fullscreen"><SpatialHero variant="analytics" title="业务统计概览" description={`${data.start} 至 ${data.end} · ${data.include_archived ? '含归档资料' : '在用资料'}`} details={[`人员档案 ${data.stock.people} 人`, `区间入境 ${data.period.entries} 人次`, `区间离境 ${data.period.exits} 人次`]}/><ReportView data={data}/></Dialog>}
  </div>;
}
