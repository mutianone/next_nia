import { useState } from 'react';
import { api } from '../api';
import { RecordFillApply } from './RecordFillApply';
import { recordError, type RecordDetail } from '../record-types';
type Review={status:string;value:string|null;template_field_id:string|null;actor?:string;at?:string;revision?:number};
type Candidate={candidate_id:string;revision:number;template_field_id:string|null;question_text:string;answer_text:string;match_method:string;candidate_status:string;problems:string[];formal_value:string;source_refs:{sentence_id:string;recognition_run_id:string;audio_id:string|null;listen_start_ms:number|null;start:number;end:number}[];review:Review|null;reviews:Review[]};
type Batch={formal_values?:Record<string,string>;id:string;stale:boolean;reason:string|null;fields:{id:string;label:string;fillable:boolean;protected:boolean}[];items:Candidate[]};
const methods:Record<string,string>={RULE:'规则匹配',ALIAS:'同义问法',SEMANTIC:'语义建议',EXPLICIT:'明确绑定',FREE_QA:'自由问答',UNMATCHED:'待选择字段'};
export function RecordFillPreview({record,draft,disabled,onApplied}:{record:RecordDetail;draft:{id:string;revision:number};disabled:boolean;onApplied?:(r:RecordDetail)=>void}){
 const [batch,setBatch]=useState<Batch|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [fields,setFields]=useState<Record<string,string>>({}),[values,setValues]=useState<Record<string,string>>({});
 const [semantic,setSemantic]=useState(false),[consent,setConsent]=useState(false);
 const [playing,setPlaying]=useState<{id:string;start:number|null}|null>(null);
 const root=`/records/${record.id}`;
 const perform=async(fn:()=>Promise<void>)=>{setBusy(true);setError('');try{await fn();}catch(e){setError(recordError(e));}finally{setBusy(false);}};
 const load=async(id?:string,discard?:string)=>{const data=await api<{batch:Batch|null}>(`${root}/fill-comparison?draft_id=${draft.id}${id?`&batch_id=${id}`:''}`);setBatch(data.batch);if(discard){setFields(v=>Object.fromEntries(Object.entries(v).filter(([k])=>k!==discard)));setValues(v=>Object.fromEntries(Object.entries(v).filter(([k])=>k!==discard)));}else{setFields({});setValues({});}};
 const review=async(c:Candidate,action:string)=>{
  await api(`${root}/fill-candidates/${c.candidate_id}/review`,{method:'POST',body:JSON.stringify({expected_revision:record.revision,candidate_revision:c.revision,action,template_field_id:fields[c.candidate_id]??c.review?.template_field_id??c.template_field_id,value:action==='MODIFIED'?values[c.candidate_id]??c.review?.value??c.answer_text:null})});
  await load(batch?.id,c.candidate_id);
 };
 const clip=record.audio_clips.find(a=>a.id===playing?.id);
 return <details onToggle={e=>{if(e.currentTarget.open&&!batch&&!busy)void perform(()=>load());}}>
  <summary>模板匹配预览</summary>
  {error&&<p role="alert">{error}</p>}
  <label><input type="checkbox" checked={semantic} onChange={e=>{setSemantic(e.target.checked);setConsent(false);}}/>使用已配置模型提供匹配建议</label>
  {semantic&&<label><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>同意发送本次问题及模板字段至已配置的云端服务</label>}
  <button type="button" disabled={busy||disabled||(semantic&&!consent)} onClick={()=>void perform(async()=>{const b=await api<Batch>(`${root}/fill-preview`,{method:'POST',body:JSON.stringify({expected_revision:record.revision,draft_id:draft.id,draft_revision:draft.revision,use_semantic:semantic,confirmed:consent})});setBatch(b);setFields({});setValues({});})}>生成匹配预览</button>
  <p>接受或修改仅保存到预览，不修改正式笔录。</p>
  {batch&&<RecordFillApply record={record} batchId={batch.id} candidates={batch.items.filter(c=>c.review&&['ACCEPTED','MODIFIED'].includes(c.review.status)).map(c=>c.candidate_id)} disabled={busy||disabled} onApplied={updated=>{setBatch(b=>b&&({...b,stale:true,reason:'正式字段已更新，请重新生成候选。'}));onApplied?.(updated);}}/>}
  {batch?.stale&&<p role="alert">{batch.reason}</p>}
  {clip&&<audio controls autoPlay key={`${playing?.id}:${playing?.start}`} src={clip.playback_url} onLoadedMetadata={e=>{e.currentTarget.currentTime=(playing?.start??0)/1000;}}/>}
  {batch?.items.map((c,i)=><article className="qa-review-turn" key={c.candidate_id}>
   <strong>{methods[c.match_method]??c.match_method} · {c.review?.status==='ACCEPTED'?'已接受':c.review?.status==='MODIFIED'?'已人工修改':c.review?.status==='REJECTED'?'已拒绝':c.problems.length?'存在冲突':'待审核'}</strong>
   {c.problems.length>0&&<p>{c.problems.map(p=>({EXCLUSIVE_FIELDS:'目标字段互斥',SHARED_SOURCE_NOT_ALLOWED:'同一回答不能用于这些字段',PROTECTED_FIELD:'字段已由人工锁定',MULTIPLE_ANSWERS_FOR_FIELD:'同一字段存在多个答案候选',NO_ANSWER:'缺少回答'}[p]??'需要核对来源')).join('；')}</p>}
   <label>目标字段<select aria-label={`候选${i+1}目标字段`} disabled={busy||disabled||batch.stale} value={fields[c.candidate_id]??c.review?.template_field_id??c.template_field_id??''} onChange={e=>setFields(v=>({...v,[c.candidate_id]:e.target.value}))}><option value="">请选择字段</option>{batch.fields.filter(f=>f.fillable).map(f=><option key={f.id} value={f.id} disabled={f.protected}>{f.label}{f.protected?'（已锁定）':''}</option>)}</select></label>
   <p style={{whiteSpace:'pre-wrap'}}>问：{c.question_text}</p><p style={{whiteSpace:'pre-wrap'}}>原答：{c.answer_text}</p>
   <p style={{whiteSpace:'pre-wrap'}}>正式字段原值：{(batch.formal_values?.[fields[c.candidate_id]??c.review?.template_field_id??c.template_field_id??'']??c.formal_value)||'—'}</p>
   <textarea aria-label={`候选${i+1}人工修改`} disabled={busy||disabled||batch.stale} value={values[c.candidate_id]??c.review?.value??c.answer_text} onChange={e=>setValues(v=>({...v,[c.candidate_id]:e.target.value}))}/>
   <button type="button" disabled={busy||disabled||batch.stale} onClick={()=>void perform(()=>review(c,'ACCEPTED'))}>接受原答</button>
   <button type="button" disabled={busy||disabled||batch.stale} onClick={()=>void perform(()=>review(c,'MODIFIED'))}>保存人工修改</button>
   <button type="button" disabled={busy||disabled||batch.stale} onClick={()=>void perform(()=>review(c,'REJECTED'))}>拒绝候选</button>
   <details><summary>来源与审核记录</summary>{c.source_refs.map((s,j)=><p key={j}>句子 {s.sentence_id} · 原文 {s.start}–{s.end}{s.audio_id&&record.audio_clips.some(a=>a.id===s.audio_id)&&<button type="button" onClick={()=>setPlaying({id:s.audio_id!,start:s.listen_start_ms})}>{s.listen_start_ms===null?'播放原音':'回听来源'}</button>}</p>)}{c.reviews.map(h=><p key={h.revision}>{h.at} · {h.actor} · {h.status} · {h.value}</p>)}</details>
  </article>)}
 </details>;
}
