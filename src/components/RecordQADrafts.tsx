import { useRef, useState } from 'react';
import { api } from '../api';
import { Dialog } from './Dialog';
import { LocalSpeechHints } from './LocalSpeechHints';
import { RecordFillPreview } from './RecordFillPreview';
import { recordError } from '../record-types';
import type { LocalSpeechHint, RecordDetail } from '../record-types';

type Role = 'OFFICER'|'INTERVIEWEE'|'OTHER'|'UNKNOWN';
type Intent = 'QUESTION'|'ANSWER'|'FOLLOW_UP'|'CONTINUATION'|'INSTRUCTION'|'CLARIFICATION'|'OTHER'|'UNKNOWN';
type Part = {start:number;end:number;role:Role;intent:Intent};
type Sentence = {local_alternatives?:LocalSpeechHint[];voice_review?:boolean;same_voice_as_previous?:boolean;listen_start_ms?:number|null;speaker_id?:string|number|null;id:string;recognition_run_id:string;text:string;parts:Part[];revision:number;audio_id:string|null;start_ms:number|null};
type Mapping = {recognition_run_id:string;channel_id:string|number|null;speaker_id:string|number;business_role:Role;revision:number};
type Material = {runs?:{id:string;origin_key:string;applied:boolean}[];revision:number;items:Mapping[];sentences:Sentence[];history:{id:string;role:Role;actor:string;at:string}[];sentence_history:{sentence_id:string;revision:number;actor:string;at:string}[]};
type Draft = {turns:{id:string;role:Role;intent:Intent;text:string}[];id:string;version:number;revision:number;status:string;stale?:boolean;pairs:{id:string;question:string;answer:string;state:string;sources:{sentence_id:string}[]}[]};
type Issue = {id:string;draft_id:string;revision:number;status:string;reason:string;suggested_action:string;sentence_ids:string[]};
const roles:Record<Role,string>={UNKNOWN:'待确认',OFFICER:'民警',INTERVIEWEE:'被询问人',OTHER:'其他人员'};
const intents:Record<Intent,string>={UNKNOWN:'待确认',QUESTION:'独立问题',ANSWER:'回答',FOLLOW_UP:'追问',CONTINUATION:'继续发言',INSTRUCTION:'告知说明',CLARIFICATION:'澄清反问',OTHER:'其他'};
const status:Record<string,string>={PROVISIONAL:'草稿',REVIEW_REQUIRED:'待核对',CONFIRMED:'已确认',QA_COMPLETED:'已配对'};

export function RecordQADrafts({record,disabled,onPending,onApplied}:{record:RecordDetail;disabled:boolean;onPending:(v:boolean)=>void;onApplied?:(r:RecordDetail)=>void}) {
  const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [material,setMaterial]=useState<Material|null>(null),[drafts,setDrafts]=useState<Draft[]>([]),[issues,setIssues]=useState<Issue[]>([]);
  const [runIds,setRunIds]=useState<string[]|null>(null);
  const [links,setLinks]=useState<Record<string,string>>({}),[crossRun,setCrossRun]=useState(false);
  const [selected,setSelected]=useState(''),[dirty,setDirty]=useState<Set<string>>(new Set());
  const [notes,setNotes]=useState<Record<string,string>>({}),[playing,setPlaying]=useState<{id:string;start:number}|null>(null);
  const inputs=useRef<Record<string,HTMLTextAreaElement|null>>({});
  const root=`/records/${record.id}`;
  const load=async(discard:string[]=[])=>{
    const [m,d,i]=await Promise.all([api<Material>(`${root}/speaker-mappings`),api<{items:Draft[]}>(`${root}/qa-drafts`),api<{items:Issue[]}>(`${root}/qa-review-issues`)]);
    setMaterial(previous=>({...m,sentences:m.sentences.map(sentence=>dirty.has(sentence.id)&&!discard.includes(sentence.id)?previous?.sentences.find(s=>s.id===sentence.id)??sentence:sentence)}));setDrafts(d.items);setIssues(i.items);setDirty(previous=>new Set([...previous].filter(id=>!discard.includes(id))));
    return m;
  };
  const perform=async(work:()=>Promise<unknown>,discard:string[]=[])=>{setBusy(true);setError('');try{await work();await load(discard);}catch(e){setError(recordError(e));}finally{setBusy(false);}};
  const post=(path:string,data:object)=>api(`${root}/${path}`,{method:'POST',body:JSON.stringify({expected_revision:material?.revision??record.revision,...data})});
  const show=async()=>{
    setRunIds(null);setOpen(true);onPending(true);setBusy(true);setError('');
    try {
      const m=await load();
      const version=m.runs?.find(r=>r.id===record.recognition_version?.id);
      if(version && !version.applied) {
        const others=m.runs!.filter(r=>r.applied && r.origin_key!==version.origin_key);
        setRunIds([...others.map(r=>r.id),version.id]);
      }
    } catch(e) {setError(recordError(e));} finally {setBusy(false);}
  };
  const close=()=>{if(busy)return;if(dirty.size){setError('请先保存逐句核对，或撤销修改。');return;}setOpen(false);onPending(false);};
  const update=(id:string,parts:Part[])=>{setMaterial(m=>m&&({...m,sentences:m.sentences.map(s=>s.id===id?{...s,parts}:s)}));setDirty(d=>new Set(d).add(id));};
  const split=(s:Sentence,index:number)=>{
    const part=s.parts[index],cursor=inputs.current[`${s.id}:${index}`]?.selectionStart??0;
    // Browser offsets are UTF-16; backend source offsets count Unicode code points.
    const text=Array.from(s.text).slice(part.start,part.end).join('');
    const offset=Array.from(text.slice(0,cursor)).length;
    if(offset<=0||offset>=part.end-part.start){setError('请将光标放在需要拆分的位置。');return;}
    const mid=part.start+offset;
    update(s.id,[...s.parts.slice(0,index),{start:part.start,end:mid,role:'UNKNOWN',intent:'UNKNOWN'},{start:mid,end:part.end,role:'UNKNOWN',intent:'UNKNOWN'},...s.parts.slice(index+1)]);
  };
  const draft=drafts.find(d=>d.id===selected)??drafts[0];
  const clip=record.audio_clips.find(a=>a.id===playing?.id);
  return <>
    <button type="button" className="btn btn-secondary" disabled={disabled} onClick={show}>角色与 QA 草稿</button>
    {open&&<Dialog title="角色与 QA 草稿" onClose={close} className="qa-review-dialog">
      {error&&<p role="alert">{error}</p>}
      {busy&&<p role="status">正在保存或读取…</p>}
      {material&&!material.sentences.length&&<p>暂无逐句识别来源</p>}
      {material&&<>
        {!!material.runs?.length&&<details><summary>识别版本</summary>{material.runs.map(run=><label key={run.id}>
          <input type="checkbox" disabled={busy||dirty.size>0} checked={runIds===null?run.applied:runIds.includes(run.id)} onChange={e=>{
            const current=runIds??material.runs!.filter(r=>r.applied).map(r=>r.id);
            setRunIds(e.target.checked?[...current.filter(id=>material.runs!.find(r=>r.id===id)?.origin_key!==run.origin_key),run.id]:current.filter(id=>id!==run.id));
          }}/>运行 {run.id.slice(0,8)}{run.applied?' · 当前正文来源':''}
        </label>)}</details>}
        <details open><summary>说话人角色</summary>
          {material.items.filter(m=>runIds===null || runIds.includes(m.recognition_run_id)).map((m,i)=><label key={JSON.stringify([m.recognition_run_id,m.channel_id,m.speaker_id])}>运行 {m.recognition_run_id.slice(0,8)} · 通道 {m.channel_id??'未知'} · 说话人 {m.speaker_id}
            <select aria-label={`说话人映射${i+1}`} value={m.business_role} disabled={busy||dirty.size>0} onChange={e=>void perform(()=>post('speaker-mappings',{recognition_run_id:m.recognition_run_id,channel_id:m.channel_id,speaker_id:m.speaker_id,business_role:e.target.value,mapping_revision:m.revision}))}>
              {Object.entries(roles).map(([value,label])=><option key={value} value={value}>{label}</option>)}
            </select>
          </label>)}
        </details>
        {clip&&<audio controls autoPlay key={`${playing?.id}:${playing?.start}`} src={clip.playback_url} onLoadedMetadata={e=>{e.currentTarget.currentTime=(playing?.start??0)/1000;}}/>}
        <details><summary>逐句核对与拆分</summary>
          <p>民警用几句话问同一题时，把主问题标为“独立问题”或“追问”，同一人对该题的铺垫和补充标为“继续发言”。另起一题时单独标记。</p>
          <div className="qa-review-turns">{material.sentences.filter(s=>runIds===null || runIds.includes(s.recognition_run_id)).map((s,si)=><article key={s.id} className="qa-review-turn">
            <header><strong>发言 {si+1}{s.speaker_id!=null && ` · 声音 ${s.speaker_id}`}</strong>{s.voice_review && <span>{s.revision ? '声音边界已人工核对' : '声音边界待回听'}</span>}{s.audio_id&&<button type="button" className="text-button" onClick={()=>setPlaying({id:s.audio_id!,start:s.listen_start_ms??s.start_ms??0})}>{s.start_ms===null?'回听原音':'回听原句'}</button>}</header>
            <LocalSpeechHints hints={s.local_alternatives} listen={s.audio_id ? ms => setPlaying({id:s.audio_id!,start:ms}) : undefined}/>
            {s.same_voice_as_previous && <span>与上一句的声音相近</span>}
            {s.parts.map((p,index)=><div key={index}>
              <label>角色<select aria-label={`发言${si+1}第${index+1}段角色`} disabled={busy} value={p.role} onChange={e=>update(s.id,s.parts.map((q,j)=>j===index?{...q,role:e.target.value as Role}:q))}>{Object.entries(roles).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>
              <label>讲话意图<select aria-label={`发言${si+1}第${index+1}段意图`} disabled={busy} value={p.intent} onChange={e=>update(s.id,s.parts.map((q,j)=>j===index?{...q,intent:e.target.value as Intent}:q))}>{Object.entries(intents).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>
              <textarea aria-label={`发言${si+1}第${index+1}段原文`} readOnly value={Array.from(s.text).slice(p.start,p.end).join('')} ref={el=>{inputs.current[`${s.id}:${index}`]=el;}}/>
              <button type="button" className="text-button" disabled={busy} onClick={()=>split(s,index)}>在光标处拆分</button>
              <button type="button" className="text-button" disabled={busy||index===0} onClick={()=>update(s.id,[...s.parts.slice(0,index-1),{start:s.parts[index-1].start,end:p.end,role:'UNKNOWN',intent:'UNKNOWN'},...s.parts.slice(index+1)])}>与上一段合并</button>
            </div>)}
            <button type="button" className="btn btn-secondary" disabled={busy||!dirty.has(s.id)} onClick={()=>void perform(()=>post(`speaker-sentences/${s.id}/review`,{sentence_revision:s.revision,parts:s.parts}),[s.id])}>保存本句核对</button>
          </article>)}</div>
        </details>
        {dirty.size>0&&<button className="text-button" disabled={busy} onClick={()=>void perform(async()=>{},[...dirty])}>撤销未保存修改</button>}
        <button type="button" className="btn btn-primary" disabled={busy||dirty.size>0||!material.sentences.length} onClick={()=>void perform(async()=>{const d=await post('qa-drafts/rebuild',runIds===null?{}:{run_ids:runIds}) as Draft;setSelected(d.id);})}>构建 QA 草稿</button>
        {draft&&<>
          <label>草稿版本<select value={draft.id} onChange={e=>setSelected(e.target.value)}>{drafts.map(d=><option key={d.id} value={d.id}>版本 {d.version} · {status[d.status]??d.status}{d.stale?' · 来源已变化':''}</option>)}</select></label>
          <details><summary>调整问答关联</summary>
            {draft.turns.filter(t=>t.role==='INTERVIEWEE'&&['ANSWER','CONTINUATION'].includes(t.intent)).map(t=><label key={t.id}>{t.text}
              <select aria-label={`回答对应问题${t.id}`} value={links[t.id]??''} onChange={e=>setLinks(l=>({...l,[t.id]:e.target.value}))}>
                <option value="">请选择对应问题</option>{draft.turns.filter(q=>q.role==='OFFICER'&&['QUESTION','FOLLOW_UP'].includes(q.intent)).map(q=><option key={q.id} value={q.id}>{q.text}</option>)}
              </select></label>)}
            <label><input type="checkbox" checked={crossRun} onChange={e=>setCrossRun(e.target.checked)}/>已核对跨批次关联</label>
            <button type="button" disabled={busy||dirty.size>0||draft.stale} onClick={()=>void perform(async()=>{
              const questions=draft.turns.filter(t=>t.role==='OFFICER'&&['QUESTION','FOLLOW_UP'].includes(t.intent));
              const answers=draft.turns.filter(t=>t.role==='INTERVIEWEE'&&['ANSWER','CONTINUATION'].includes(t.intent));
              if(answers.some(a=>!links[a.id]))throw new Error('请选择每段回答对应的问题。');
              const pairs=questions.map(q=>({question_turn_ids:[q.id],answer_turn_ids:answers.filter(a=>links[a.id]===q.id).map(a=>a.id),other_turn_ids:[] as string[],confirm_cross_run:crossRun}));
              const used=new Set([...questions,...answers].map(t=>t.id));
              draft.turns.filter(t=>!used.has(t.id)).forEach(t=>pairs.push({question_turn_ids:[],answer_turn_ids:[],other_turn_ids:[t.id],confirm_cross_run:false}));
              const saved=await post(`qa-drafts/${draft.id}/pairs/review`,{draft_revision:draft.revision,pairs}) as Draft;setSelected(saved.id);setLinks({});
            })}>保存关联为新草稿</button>
          </details>
          {draft.status==='CONFIRMED'&&<RecordFillPreview key={`${draft.id}:${draft.revision}`} record={{...record,revision:material.revision}} draft={draft} onApplied={updated=>{onApplied?.(updated);void load();}} disabled={busy||dirty.size>0||!!draft.stale||disabled}/>}
          {draft.pairs.map(p=><article key={p.id} className="qa-review-turn"><strong>{status[p.state]??'待核对'}</strong><p>问：{p.question||'—'}</p><p>答：{p.answer||'—'}</p><details><summary>来源原文</summary>{Array.from(new Set(p.sources.map(s=>s.sentence_id))).map(id=><blockquote key={id}>{material.sentences.find(s=>s.id===id)?.text??'来源不可用'}</blockquote>)}</details></article>)}
          {issues.filter(i=>i.draft_id===draft.id).map(i=><article key={i.id} className="qa-review-turn"><p>{i.reason}</p><p>{i.suggested_action}</p>{i.status==='OPEN'?<><input aria-label={`处理结果${i.id}`} value={notes[i.id]??''} onChange={e=>setNotes(n=>({...n,[i.id]:e.target.value}))}/><button type="button" disabled={busy||!notes[i.id]?.trim()} onClick={()=>void perform(()=>post(`qa-review-issues/${i.id}/resolve`,{issue_revision:i.revision,note:notes[i.id]}))}>记录处理结果</button></>:<span>已处理</span>}</article>)}
          <button type="button" className="btn btn-primary" disabled={busy||dirty.size>0||draft.stale||draft.status==='CONFIRMED'||issues.some(i=>i.draft_id===draft.id&&i.status==='OPEN')} onClick={()=>void perform(()=>post(`qa-drafts/${draft.id}/confirm`,{draft_revision:draft.revision}))}>确认 QA 草稿</button>
        </>}
        <details><summary>修改记录</summary>{material.history.map(h=><p key={h.id}>{h.at} · {h.actor} · {roles[h.role]}</p>)}{material.sentence_history.map(h=><p key={`${h.sentence_id}:${h.revision}`}>{h.at} · {h.actor} · 逐句修订 v{h.revision}</p>)}</details>
      </>}
      <button className="btn btn-secondary" disabled={busy} onClick={close}>关闭</button>
    </Dialog>}
  </>;
}
