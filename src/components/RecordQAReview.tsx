import { RecordDualReview } from './RecordDualReview';
import { useRef, useState } from 'react';
import { api } from '../api';
import { Dialog } from './Dialog';
import { confirmAction } from '../lib/confirmation';
import { recordError, speakerLabels } from '../record-types';
import type { RecordDetail, SegmentInput, Speaker } from '../record-types';
import './RecordQAReview.css';
import { RecordQADrafts } from './RecordQADrafts';

type Turn = {voice_group?:string|null;voice_label?:string|null;source_id:string;source_start:number;source_end:number;text:string;suggested_role:Speaker;speaker:Speaker;audio_id:string|null;listen_start_ms:number|null;start_ms:number|null;end_ms:number|null};
type Proposal = {revision:number;turns:Turn[];issues:{segment_id:string;message:string}[]};
export function RecordQAReview({record,disabled,onApplied,onPending}:{record:RecordDetail;disabled:boolean;onApplied:(r:RecordDetail)=>void;onPending:(v:boolean)=>void}) {
  const [proposal,setProposal]=useState<Proposal|null>(null);
  const [repairs,setRepairs]=useState<Record<number,{text:string;start_ms:number;end_ms:number}>>({});
  const [open,setOpen]=useState(false);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const [playing,setPlaying]=useState<{id:string;start:number}|null>(null);
  const inputs=useRef<(HTMLTextAreaElement|null)[]>([]);
  const close=()=>{if(busy)return;setOpen(false);setProposal(null);onPending(false);};
  const prepare=async()=>{
    if(disabled)return;
    if(!await confirmAction('将当前已保存文字发送至已配置云端模型，生成待核对的问答分句。',{title:'整理问答',confirmLabel:'开始整理',tone:'info'}))return;
    setOpen(true);onPending(true);setBusy(true);setError('');setPlaying(null);setRepairs({});
    try{setProposal(await api<Proposal>(`/records/${record.id}/qa-proposal`,{method:'POST',body:JSON.stringify({expected_revision:record.revision,confirmed:true})}));}
    catch(e){setError(recordError(e));}finally{setBusy(false);}
  };
  const change=(index:number,patch:Partial<Turn>)=>setProposal(p=>p&&({...p,turns:p.turns.map((t,i)=>i===index?{...t,...patch}:t)}));
  const split=(index:number)=>{
    if(!proposal)return;const t=proposal.turns[index];const at=inputs.current[index]?.selectionStart??0;
    if(at<=0||at>=t.text.length){setError('请把光标放在需要拆分的位置。');return;}
    setRepairs({});
    const turns=proposal.turns.map(turn=>t.voice_group&&turn.voice_group===t.voice_group?{...turn,voice_group:null}:turn);
    const parts=[t.text.slice(0,at),t.text.slice(at)].map(text=>({...t,text,speaker:'unknown' as Speaker,suggested_role:'unknown' as Speaker,voice_group:null,start_ms:null,end_ms:null}));
    setProposal({...proposal,turns:[...turns.slice(0,index),...parts,...turns.slice(index+1)]});setError('');
  };
  const merge=(index:number)=>{
    if(!proposal||index===0)return;const before=proposal.turns[index-1],current=proposal.turns[index];
    if(before.source_id!==current.source_id)return;setRepairs({});
    setProposal({...proposal,turns:[...proposal.turns.slice(0,index-1),{...before,text:before.text+current.text,speaker:'unknown',suggested_role:'unknown',voice_group:null,start_ms:null,end_ms:null},...proposal.turns.slice(index+1)]});
  };
  const repair=async(index:number)=>{
    if(!proposal||busy)return;
    if(!await confirmAction('将此段原音发送至已配置的语音服务重新识别。',{title:'补转写',confirmLabel:'开始识别',tone:'info'}))return;
    setBusy(true);setError('');
    try{const result=await api<{text:string;start_ms:number;end_ms:number}>(`/records/${record.id}/qa-retranscribe`,{method:'POST',body:JSON.stringify({expected_revision:proposal.revision,segment_id:proposal.turns[index].source_id,confirmed:true})});setRepairs(p=>({...p,[index]:result}));}
    catch(e){setError(recordError(e));}finally{setBusy(false);}
  };
  const apply=async()=>{
    if(!proposal||busy||proposal.turns.some(t=>t.speaker==='unknown'||!t.text.trim()))return;
    setBusy(true);setError('');
    const seen=new Set<string>();
    const segments:SegmentInput[]=proposal.turns.map(t=>{
      const first=!seen.has(t.source_id);seen.add(t.source_id);
      const marker=t.speaker==='interviewer'?'问：':t.speaker==='interviewee'?'答：':'翻译：';
      return {...(first?{id:t.source_id}:{}),audio_id:t.audio_id,start_ms:t.start_ms,end_ms:t.end_ms,speaker:t.speaker,
        text:marker+t.text.trim().replace(/^\[说话人 [A-Za-z0-9_-]{1,32}\]\s*/, '').replace(/^(问|答|询问人|被询问人|民警|当事人|翻译)[:：]\s*/, '')};
    });
    try{
      const updated=await api<RecordDetail>(`/records/${record.id}/segments`,{method:'PATCH',body:JSON.stringify({expected_revision:proposal.revision,segments})});
      onApplied(updated);setOpen(false);setProposal(null);onPending(false);
    }catch(e){setError(recordError(e));}finally{setBusy(false);}
  };
  const clip=record.audio_clips.find(c=>c.id===playing?.id);
  return <><RecordDualReview record={record} disabled={disabled} onPending={onPending} onApplied={onApplied}/><RecordQADrafts record={record} disabled={disabled||record.segments.some(s=>s.dual_review)} onPending={onPending} onApplied={onApplied}/><button type="button" className="btn btn-primary" disabled={disabled||!record.segments.length||record.segments.some(s=>s.dual_review)} onClick={()=>void prepare()}>整理问答</button>
    {open&&<Dialog title="问答分句与角色核对" onClose={close} className="qa-review-dialog">
      {busy&&!proposal&&<p role="status">正在整理问答…</p>}
      {error&&<p role="alert">{error}</p>}
      {proposal&&<>
        <button type="button" className="btn btn-secondary" disabled={busy||!proposal.turns.some(t=>t.speaker==='unknown'&&t.suggested_role!=='unknown')} onClick={async()=>{if(await confirmAction('请先核对分句和角色。采用建议后仍可逐段修改，不会自动保存。',{title:'采用角色建议',confirmLabel:'已核对，采用建议',tone:'info'}))setProposal(p=>p&&({...p,turns:p.turns.map(t=>t.speaker==='unknown'&&t.suggested_role!=='unknown'?{...t,speaker:t.suggested_role}:t)}));}}>采用已核对的角色建议</button>
        {Array.from(new Set(proposal.turns.map(t=>t.voice_group).filter((g):g is string=>!!g))).map((group,index)=><label key={group}>声音 {index+1} 的角色
          <select aria-label={`声音${index+1}角色`} disabled={busy} value={proposal.turns.find(t=>t.voice_group===group)?.speaker??'unknown'} onChange={e=>{const speaker=e.target.value as Speaker;setProposal(p=>p&&({...p,turns:p.turns.map(t=>t.voice_group===group?{...t,speaker}:t)}));}}>
            {Object.entries(speakerLabels).map(([value,label])=><option key={value} value={value}>{label}</option>)}
          </select></label>)}
        {proposal.issues.map((issue,i)=><p className="rec-warning" key={i}>{issue.message}</p>)}
        {clip&&<audio key={`${clip.id}-${playing?.start}`} controls autoPlay src={clip.playback_url} onLoadedMetadata={e=>{e.currentTarget.currentTime=(playing?.start||0)/1000;}}/>}
        <div className="qa-review-turns">{proposal.turns.map((t,i)=><article key={i} className="qa-review-turn">
          <header><strong>发言 {i+1}</strong><span>建议角色：{speakerLabels[t.suggested_role]}</span>
            {t.audio_id&&<button type="button" className="text-button" onClick={()=>setPlaying({id:t.audio_id!,start:t.listen_start_ms||0})}>{t.listen_start_ms===null?'回听对应原音':'回听来源片段'}</button>}
          </header>
          <label>确认角色<select aria-label={`发言${i+1}角色`} value={t.speaker} disabled={busy} onChange={e=>change(i,{speaker:e.target.value as Speaker})}>{Object.entries(speakerLabels).map(([v,label])=><option key={v} value={v}>{label}</option>)}</select></label>
          <textarea aria-label={`发言${i+1}文字`} ref={el=>{inputs.current[i]=el;}} value={t.text} disabled={busy} onChange={e=>change(i,{text:e.target.value})}/>
          <div><button className="text-button" type="button" disabled={busy} onClick={()=>split(i)}>在光标处拆分</button><button className="text-button" type="button" disabled={busy||i===0||proposal.turns[i-1].source_id!==t.source_id} onClick={()=>merge(i)}>与上一段合并</button></div>
          {t.start_ms!==null&&t.end_ms!==null&&<button className="text-button" type="button" disabled={busy} onClick={()=>void repair(i)}>原音补转写</button>}
          {repairs[i]&&<div><p>补转写候选</p><blockquote>{repairs[i].text}</blockquote><button type="button" className="text-button" disabled={busy} onClick={()=>{change(i,{text:repairs[i].text,start_ms:repairs[i].start_ms,end_ms:repairs[i].end_ms});setRepairs(p=>{const next={...p};delete next[i];return next;});}}>采用候选</button><button type="button" className="text-button" disabled={busy} onClick={()=>setRepairs(p=>{const next={...p};delete next[i];return next;})}>保留原文</button></div>}
          <details><summary>查看来源原文</summary><blockquote>{record.segments.find(s=>s.id===t.source_id)?.text}</blockquote></details>
        </article>)}</div>
        <button type="button" className="btn btn-primary" disabled={busy||!proposal.turns.length||proposal.turns.some(t=>t.speaker==='unknown'||!t.text.trim())} onClick={()=>void apply()}>确认并保存修订</button>
      </>}
      <button type="button" className="btn btn-secondary" disabled={busy} onClick={close}>取消</button>
    </Dialog>}
  </>;
}
