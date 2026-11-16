import { useCallback, useEffect, useState, useRef, type CSSProperties } from 'react';
import { api } from '../api';
import { usePermissions } from '../auth/AuthContext';
import { PageHeading } from '../components/PageHeading';
import { Dialog } from '../components/Dialog';
import { usePageDirty } from '../hooks/usePageDirty';
import { confirmAction } from '../lib/confirmation';
import type { AlertItem, AlertList } from '../alert-types';
import './ExpiryAlerts.css';

const signatureLabels: Record<string,string> = {organization:'办案单位：',officer:'承办人签名：',approver:'批准人签名：',document_date:'日期：'};
type WordRun = {kind:string;key:string;text:string;style:CSSProperties};
type Decision = { layout?:{page:CSSProperties;paragraphs:{style:CSSProperties;runs:WordRun[]}[]}; id:string; revision:number; fields:Record<string,string>; labels:Record<string,string>; paragraphs:string[]; missing:string[] };
function WordField({value,original,label,style,disabled,onChange}:{value:string;original:string;label:string;style:CSSProperties;disabled:boolean;onChange:(value:string)=>void}) {
 const ref=useRef<HTMLTextAreaElement>(null);
 const width=Math.max(4, Math.min(32, [...(value||original)].reduce((n,c)=>n+(c.charCodeAt(0)>255?1:.6),0)+.7));
 useEffect(()=>{const field=ref.current;if(field){field.style.height='auto';field.style.height=`${Math.max(field.scrollHeight,28)}px`;}},[value,width]);
 return <textarea ref={ref} aria-label={label} title={label} className="word-inline-input" style={{...style,width:`${width}em`}} readOnly={disabled} rows={1} value={value} placeholder={original.trim()||' '} onChange={e=>onChange(e.target.value)} spellCheck={false}/>;
}
export function DecisionEditor({ alertId,onClose }: {alertId:string;onClose:()=>void}) {
  const [doc,setDoc]=useState<Decision|null>(null),[fields,setFields]=useState<Record<string,string>>({}),[error,setError]=useState(''),[busy,setBusy]=useState(false),[notice,setNotice]=useState('');
  const {write}=usePermissions();
  const dirty=!!doc && JSON.stringify(fields)!==JSON.stringify(doc.fields);
  usePageDirty(dirty || busy);
  useEffect(()=>{let live=true;api<Decision>(`/alerts/${alertId}/decision`).then(d=>{if(live){setDoc(d);setFields(d.fields);}}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[alertId]);
  async function close(){if(busy)return;if(dirty&&!await confirmAction('文书修改尚未保存，确定离开吗？',{title:'保留文书修改',confirmLabel:'放弃修改',cancelLabel:'继续编辑'}))return;onClose();}
  async function save(){if(!doc)return;setBusy(true);setError('');setNotice('');try{const d=await api<Decision>(`/alerts/${alertId}/decision`,{method:'PUT',body:JSON.stringify({expected_revision:doc.revision,fields})});setDoc(d);setFields(d.fields);setNotice('已保存。下载的 DOCX 将包含本次修改。');}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function download(){setBusy(true);setError('');try{const r=await fetch(`/api/alerts/${alertId}/decision.docx`);if(!r.ok){const e=await r.json();throw new Error(e.detail||'下载失败');}const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download='责令限期出境决定书_草稿.docx';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <Dialog title="责令限期出境决定书 · 文档内编辑" className="expiry-document-dialog" onClose={close}>
    <div className="expiry-editor-toolbar"><span>点击正文中的浅色字段直接填写 · 草稿未签发</span><button className="btn btn-primary" disabled={!write||!doc||busy||!dirty} onClick={save}>保存修改</button><button className="btn btn-secondary" disabled={!write||!doc||busy||dirty} onClick={download}>下载 DOCX</button></div>
    {error&&<p role="alert" className="auth-error">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {!doc&&!error?<p role="status">正在打开文书…</p>:doc&&doc.layout?<div className="word-page-scroll"><article className="expiry-paper word-source-layout" style={doc.layout.page} aria-label="原模板文书正文">{doc.layout.paragraphs.map((paragraph,index)=>{
 const signature=paragraph.runs.find(run=>run.kind==='field' && signatureLabels[run.key]);
 if(signature && paragraph.runs.filter(run=>run.kind==='field').length===1) return <p key={index} className="word-signature-row" style={{...paragraph.style,textIndent:0,textAlign:'left'}}><span className="word-signature-label" style={signature.style}>{signatureLabels[signature.key]}</span><WordField label={doc.labels[signature.key]||signature.key} style={signature.style} value={fields[signature.key]||''} original={signature.text} disabled={!write||busy} onChange={value=>{setFields(v=>({...v,[signature.key]:value}));setNotice('');}}/><span className="word-signature-seal" style={signature.style}>{signature.key==='organization'?'（盖章）':''}</span></p>;
 return <p key={index} style={paragraph.style}>{paragraph.runs.length?paragraph.runs.map((run,i)=>run.kind==='field'?<WordField key={i} label={doc.labels[run.key]||run.key} style={run.style} value={fields[run.key]||''} original={run.text} disabled={!write||busy} onChange={value=>{setFields(v=>({...v,[run.key]:value}));setNotice('');}}/>:<span key={i} style={run.style}>{run.text}</span>):<br/>}</p>;})}</article></div>:null}

  </Dialog>;
}
export function Alerts({initialId,onContextConsumed}: {initialId?:string|null;onContextConsumed?:()=>void}) {
 const [data,setData]=useState<AlertList|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[query,setQuery]=useState(''),[page,setPage]=useState(1),[documentId,setDocumentId]=useState<string|null>(null);
 const {write}=usePermissions();
 const load=useCallback(async()=>{try{setData(await api<AlertList>(`/alerts?status=active&page_size=20&page=${page}&q=${encodeURIComponent(query)}`));setError('');}catch(e){setError((e as Error).message);}},[page,query]);
 useEffect(()=>{void load();const timer=setInterval(()=>void load(),15000);return()=>clearInterval(timer);},[load]);
 useEffect(()=>{if(initialId){setQuery('');setPage(1);onContextConsumed?.();}},[initialId,onContextConsumed]);
 async function scan(){setBusy(true);try{await api('/alerts/scan',{method:'POST'});await load();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <div className="page-content"><PageHeading title="签证逾期预警" description="同步人员档案有效期，提前三天预警，逾期满七天自动生成文书草稿。"><button className="btn btn-primary" disabled={!write||busy} onClick={scan}>{busy?'扫描中…':'立即扫描'}</button></PageHeading>
 <p className="muted">每分钟自动扫描 · 列表每 15 秒刷新 · 日期以人员档案中的证件有效期为准</p>
 {error&&<p role="alert" className="auth-error">{error}</p>}
 <div className="expiry-summary"><span>即将到期 <b>{data?.stats.due_soon??0}</b></span><span>今日到期 <b>{data?.stats.due_today??0}</b></span><span>已经逾期 <b>{data?.stats.past_due??0}</b></span></div>
 <input className="input" aria-label="搜索预警人员" placeholder="搜索人员姓名或档案编号" value={query} onChange={e=>{setQuery(e.target.value);setPage(1);}}/>
 <section className="expiry-list" aria-label="签证预警列表">{data?.items.map((item:AlertItem)=><article key={item.id} className="expiry-alert"><div><h3>{item.person_name} <small>{item.person_code}</small></h3><p>有效期至 {item.deadline}</p><strong>{item.days_remaining!<0?`已逾期 ${-item.days_remaining!} 天`:item.days_remaining===0?'今日到期':`还有 ${item.days_remaining} 天到期`}</strong><p>{item.days_remaining!<=-7?'已生成文书草稿，可直接在文书版式中补齐缺项。':'已发出到期预警，请及时处理。'}</p></div>{item.days_remaining!==null&&item.days_remaining<=-7&&<button className="btn btn-primary" onClick={()=>setDocumentId(item.id)}>打开并编辑决定书</button>}</article>)}</section>
 {data&&!data.items.length&&<p className="panel">当前没有符合条件的到期预警。</p>}
 <div className="expiry-pagination"><button className="btn btn-secondary" disabled={page===1} onClick={()=>setPage(p=>p-1)}>上一页</button><span>第 {page} 页 · 共 {data?.total??0} 条</span><button className="btn btn-secondary" disabled={!data||page*20>=data.total} onClick={()=>setPage(p=>p+1)}>下一页</button></div>
 {documentId&&<DecisionEditor alertId={documentId} onClose={()=>setDocumentId(null)}/>}
 </div>;
}
