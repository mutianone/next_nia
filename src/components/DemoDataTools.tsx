import { useEffect, useRef, useState } from 'react';
import { CheckCheck, DatabaseZap, Layers3, LoaderCircle, RefreshCw, Trash2 } from 'lucide-react';
import { api } from '../api';
import { confirmAction } from '../lib/confirmation';
import { usePageDirty } from '../hooks/usePageDirty';

type Dataset = { enabled: boolean; active: boolean; batch_id: string | null; created_at: string | null; counts: Record<string,number> };
const modules = [['people','人员档案'],['person_stays','停留记录'],['alerts','期限提醒'],['knowledge_documents','法规资料'],['record_sessions','询问笔录'],['record_audio','询问录音'],['document_drafts','业务文书'],['profile_facts','画像事实'],['profile_relations','人员关系']];
export function DemoDataTools({ onChange, disabled=false }: { onChange:()=>void; disabled?:boolean }) {
  const [data,setData]=useState<Dataset|null>(null),[busy,setBusy]=useState(''),[error,setError]=useState(''),[message,setMessage]=useState('');
  const active = useRef(true);
  usePageDirty(Boolean(busy));
  async function refresh(){try{const next=await api<Dataset>('/demo-data');if(active.current)setData(next);}catch(e){if(active.current)setError(e instanceof Error?e.message:'读取失败，请重试。');}}
  useEffect(()=>{active.current=true;void refresh();return()=>{active.current=false;};},[]);
  async function perform(action:'generate'|'clear') {
    const agreed=await confirmAction(action==='generate'
      ? '将在测试数据库中生成一组相互关联的虚构业务资料，覆盖人员、预警、法规、文书、笔录、录音和画像。业务页面使用自然内容，不显示演示标签；已有资料与 AI 配置保持原样。'
      : '清理当前生成批次及其后续修订、录音和历史记录。原有人员、资料、系统设置与密钥不受影响。如果新增独立业务引用了这些资料，会阻止清理。此操作无法撤销，可先下载完整备份。',
      {title:action==='generate'?'准备完整演示资料':'清理本批次演示资料',confirmLabel:action==='generate'?'一键生成':'确认清理',tone:action==='clear'?'danger':'info'});
    if(!agreed)return;
    setBusy(action);setError('');setMessage('');
    try {
      const result=await api<Dataset>('/demo-data/'+action,{method:'POST',body:JSON.stringify({confirmed:true,batch_id:data?.batch_id})});
      setData(previous=>({...result,enabled:previous?.enabled??false}));
      setMessage(action==='generate'?'资料已准备好，可以进入各业务页面查看完整效果。':'本批次资料已清理，原有业务数据已保留。');onChange();
    } catch(e){setError(e instanceof Error?e.message:'操作未完成，请重试。');await refresh();}
    finally{setBusy('');}
  }
  return <section className="panel demo-tools">
    <div className="demo-tools-heading"><span className="demo-tools-symbol"><DatabaseZap size={25}/></span><div><h2>演示资料工作台</h2><p>一次准备，让每个业务页面都有完整内容。</p></div><button type="button" className="icon-button" aria-label="刷新演示资料状态" disabled={!!busy} onClick={()=>void refresh()}><RefreshCw size={15}/></button></div>
    <div className="demo-tools-note"><Layers3 size={17}/><p>以当前日期构建关联资料、近期趋势和办理进度。所有内容在本地生成，内部保留批次标识；不调用 AI，不使用真实人员资料。</p></div>
    {error&&<div className="p5-notice error" role="alert">{error}</div>}{message&&<div className="p5-notice" role="status"><CheckCheck size={16}/>{message}</div>}
    <div className="demo-module-grid">{modules.map(([key,label])=><div key={key}><span>{label}</span><strong>{data?.active?data.counts[key]??0:'—'}</strong></div>)}</div>
    <div className="demo-tools-footer"><div><span className={`demo-state ${data?.active?'ready':''}`}/><span>{data?.active?'资料已就绪':data?.enabled?'尚未生成资料':'仅测试版可用'}{data?.active&&data.created_at&&<small>{new Date(data.created_at).toLocaleString('zh-CN')} · 当前批次</small>}</span></div><div className="p5-actions"><button type="button" className="btn btn-secondary" disabled={!data?.enabled||!data.active||!!busy||disabled} onClick={()=>void perform('clear')}>{busy==='clear'?<LoaderCircle className="spinning" size={15}/>:<Trash2 size={15}/>}清理演示资料</button><button type="button" className="btn btn-primary" disabled={!data?.enabled||data.active||!!busy||disabled} onClick={()=>void perform('generate')}>{busy==='generate'?<LoaderCircle className="spinning" size={15}/>:<DatabaseZap size={15}/>}一键生成演示资料</button></div></div>
  </section>;
}
