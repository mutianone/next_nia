import { useState } from 'react';
import { api } from '../api';
import { Dialog } from './Dialog';
import { recordError } from '../record-types';
import type { RecordDetail } from '../record-types';

type Engine = 'siliconflow'|'volcengine';
type Candidate = {provider:Engine;text:string;segments:{text:string;start_ms?:number|null;end_ms?:number|null;speaker_id?:string|number|null}[]};
type Group = {group:number;status:string;candidates:Candidate[];errors:Partial<Record<Engine,{message:string}>>};
type Run = {id:string;audio_id:string;applied:boolean;groups:Group[]};
type Material = {revision:number;runs:Run[]};
const names:Record<Engine,string> = {siliconflow:'Qwen3-ASR（硅基流动）',volcengine:'火山豆包 2.0'};
const statuses:Record<string,string> = {text_agrees:'文字一致，角色仍须确认',conflict:'文字有差异，请回听',single:'此段只有一方候选，注意漏转',untimed:'时间依据不足，按整段核对'};

export function RecordDualReview({record,disabled,onApplied,onPending}:{record:RecordDetail;disabled:boolean;onApplied:(r:RecordDetail)=>void;onPending:(v:boolean)=>void}) {
  const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [material,setMaterial]=useState<Material|null>(null),[selected,setSelected]=useState('');
  const [choices,setChoices]=useState<Record<number,Engine>>({}),[confirmed,setConfirmed]=useState(false);
  const [playing,setPlaying]=useState<number|null>(null);
  const run=material?.runs.find(r=>r.id===selected);
  const clip=record.audio_clips.find(c=>c.id===run?.audio_id);
  const show=async()=>{
    setOpen(true);onPending(true);setBusy(true);setError('');setChoices({});setConfirmed(false);setPlaying(null);
    try {const m=await api<Material>(`/records/${record.id}/dual-review`);setMaterial(m);setSelected((m.runs.find(r=>r.applied)??m.runs.at(-1))?.id??'');}
    catch(e){setError(recordError(e));}finally{setBusy(false);}
  };
  const close=()=>{if(!busy){setOpen(false);onPending(false);}};
  const apply=async()=>{
    if(!run||!material||!confirmed||busy||run.groups.some(g=>!choices[g.group]))return;
    setBusy(true);setError('');
    try {const result=await api<RecordDetail>(`/records/${record.id}/dual-review/${run.id}`,{method:'POST',body:JSON.stringify({expected_revision:material.revision,confirmed:true,choices:run.groups.map(g=>choices[g.group])})});onApplied(result);setOpen(false);onPending(false);}
    catch(e){setError(recordError(e));}finally{setBusy(false);}
  };
  if(!record.transcription_attempts?.some(a=>a.provider==='dual')&&!record.segments.some(s=>s.dual_review))return null;
  return <><button type="button" className="btn btn-primary" disabled={disabled} onClick={()=>void show()}>双引擎核对</button>
    {open&&<Dialog title="双引擎原文核对" onClose={close} className="qa-review-dialog">
      <p>请回听后逐段选择。文字一致也可能同时识别错误；两家的声音编号互不对应。选择后再在“整理问答”或“角色与 QA 草稿”确认角色、拆分问答及修订错字。</p>
      {error&&<p role="alert">{error}</p>}{busy&&<p role="status">正在读取或保存…</p>}
      {!!material?.runs.length&&<label>识别版本<select disabled={busy} value={selected} onChange={e=>{setSelected(e.target.value);setChoices({});setConfirmed(false);setPlaying(null);}}>{material.runs.map(r=><option key={r.id} value={r.id}>{r.id.slice(0,8)}{r.applied?' · 待核对正文':' · 保留版本'}</option>)}</select></label>}
      {material&&!material.runs.length&&<p>暂无双引擎识别结果。</p>}
      {clip&&playing!==null&&<audio controls autoPlay key={`${selected}-${playing}`} src={clip.playback_url} onLoadedMetadata={e=>{e.currentTarget.currentTime=playing/1000;}}/>}
      {run?.groups.map(g=><article key={g.group} className="qa-review-turn">
        <header><strong>片段 {g.group+1} · {statuses[g.status]??g.status}</strong>{clip&&<button type="button" className="text-button" onClick={()=>{const times=g.candidates.flatMap(c=>c.segments.map(s=>s.start_ms)).filter((t):t is number=>typeof t==='number');setPlaying(times.length?Math.min(...times):0);}}>回听片段 {g.group+1}</button>}</header>
        {Object.entries(g.errors).map(([engine,err])=><p role="status" key={engine}>{names[engine as Engine]}未返回结果：{err?.message}。当前结果不构成双引擎一致。</p>)}
        {g.candidates.map(c=><div key={c.provider}><label><input type="radio" name={`dual-${g.group}`} disabled={busy} checked={choices[g.group]===c.provider} onChange={()=>{setChoices(p=>({...p,[g.group]:c.provider}));setConfirmed(false);}}/>片段 {g.group+1} 采用 {names[c.provider]}</label>
          {c.segments.map((s,i)=><blockquote key={i}><small>{s.speaker_id!=null?`本引擎声音 ${s.speaker_id}`:'声音未知'}{s.start_ms!=null&&s.end_ms!=null?` · ${(s.start_ms/1000).toFixed(2)}–${(s.end_ms/1000).toFixed(2)} 秒`:''}</small><p>{s.text}</p></blockquote>)}
        </div>)}
      </article>)}
      <label><input type="checkbox" disabled={busy} checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我已回听并核对每段候选，保留短答、否定和补充内容；业务角色将另行确认。</label>
      <div><button type="button" className="btn btn-primary" disabled={busy||!confirmed||!run||run.groups.some(g=>!choices[g.group])} onClick={()=>void apply()}>保存所选版本，继续核对角色</button><button type="button" className="btn btn-secondary" disabled={busy} onClick={close}>取消</button></div>
    </Dialog>}
  </>;
}
