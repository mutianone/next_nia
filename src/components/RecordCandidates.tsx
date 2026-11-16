import { useState } from 'react';
import { api } from '../api';
import { Dialog } from './Dialog';
import { confirmAction } from '../lib/confirmation';
import { recordError, recordTime } from '../record-types';
import type { RecordDetail, FieldCandidate, CandidateEvidence } from '../record-types';
import './RecordCandidates.css';

const labels: Record<string,string> = {pending:'待核对',conflict:'前后内容有差异',confirmed:'已采用',rejected:'已驳回',manual:'已人工修订确认',stale:'来源已变化'};
export function RecordCandidates({record, canReview, busy, onBusy, onApply, onError, onPlay}: {
  record:RecordDetail;canReview:boolean;busy:boolean;onBusy:(value:string)=>void;
  onApply:(value:RecordDetail)=>void;onError:(value:string)=>void;onPlay:(id:string,seconds:number)=>void;
}) {
  const [evidence,setEvidence]=useState<CandidateEvidence|null>(null);
  const candidates=record.candidates || [];
  const order=record.template_snapshot?.fields?.map(f=>f.id) || [];
  const latest=candidates.filter(c=>c.latest).sort((a,b)=>order.indexOf(a.field_id)-order.indexOf(b.field_id));
  const history=candidates.filter(c=>!c.latest);
  const [note,setNote]=useState('');
  const [onlyAttention,setOnlyAttention]=useState(false);
  const checks=record.template_snapshot?.quality_checks||[];
  const missing=(record.template_snapshot?.fields||[]).filter(f=>f.id.startsWith('q')&&!record.template_snapshot?.values?.[f.id]);
  const attention=(c:FieldCandidate)=>c.status==='conflict'||c.sources.some(s=>s.precision!=='exact')||checks.some(x=>x.field_id===c.field_id);
  const visible=onlyAttention?latest.filter(attention):latest;
  async function review(candidate:FieldCandidate,decision:'confirmed'|'rejected') {
    if(!canReview || busy)return;
    const replace=candidate.protected && candidate.current_value!==candidate.value;
    if(decision==='confirmed' && replace && !(await confirmAction('当前字段有人工保护值。采用这条候选会替换当前值，历史版本仍保留。',{title:'确认替换人工保护值',confirmLabel:'采用新候选'}))) return;
    if(decision==='rejected' && !(await confirmAction('驳回后，这条候选不会用于成文。尚未人工修改的字段恢复到生成前的值；已受保护的人工内容保留。',{title:'驳回候选',confirmLabel:'确认驳回'})))return;
    onBusy('candidate');onError('');
    try {onApply(await api<RecordDetail>(`/records/${record.id}/candidates/${candidate.id}/review`,{method:'POST',body:JSON.stringify({expected_revision:record.revision,decision,note,replace_protected:replace})}));setNote('');}
    catch(error){onError(recordError(error));}finally{onBusy('');}
  }
  async function showEvidence(candidate:FieldCandidate) {
    onBusy('candidate');onError('');
    try {setEvidence(await api<CandidateEvidence>(`/records/${record.id}/candidates/${candidate.id}/evidence`));}
    catch(error){onError(recordError(error));}finally{onBusy('');}
  }
  const card=(c:FieldCandidate)=><article className="candidate-card" key={c.id}>
    <header><strong>{record.template_snapshot?.fields?.find(f=>f.id===c.field_id)?.label || c.field_id}</strong><span className={c.status==='conflict'?'rec-warning':'rec-muted'}>{labels[c.status] || c.status}{!c.latest?' · 历史版本':''}</span></header>
    <small className="rec-muted">{c.origin==='rule'?'标准问题精确匹配':'云端 AI 候选'} · {recordTime(c.created_at)}</small>
    {c.latest && c.status==='conflict' && <p className="rec-warning">存在不同陈述，请核对原文后决定；差异提示不代表已判断哪一条正确。</p>}
    {c.previous_value && c.previous_value!==c.value && <div><small>生成前的内容</small><p>{c.previous_value}</p></div>}
    {c.protected && c.current_value!==c.value && <div><small>当前人工保护内容</small><p>{c.current_value || '（人工留空）'}</p></div>}
    <div className="candidate-comparison"><div><small>本次候选</small><p>{c.value}</p></div><div><small>原文依据</small>{c.evidence_preview?.length?c.evidence_preview.map((s,i)=><blockquote key={i}>{s.question?<><span>问：{s.question}</span><p>答：{s.quote}</p></>:<p>{s.quote}</p>}</blockquote>):<p className="rec-muted">此候选只有段落引用，请打开原文核对。</p>}</div></div>
    {checks.filter(x=>x.field_id===c.field_id).map((x,i)=><p className="rec-warning" key={i}>{x.message}</p>)}
    <footer><button className="btn btn-secondary" disabled={busy} onClick={()=>void showEvidence(c)}>查看原文依据</button>
      {c.latest && ['pending','conflict'].includes(c.status) && <><button className="btn btn-primary" disabled={!canReview || busy} onClick={()=>void review(c,'confirmed')}>采用并保护</button><button className="btn btn-secondary" disabled={!canReview || busy} onClick={()=>void review(c,'rejected')}>驳回</button></>}
    </footer>
    {c.reviews.map((review,i)=><small className="rec-muted" key={i}>{labels[review.decision] || review.decision} · {recordTime(review.created_at)}{review.note && ` · ${review.note}`}</small>)}
  </article>;
  return <section className="candidate-review" aria-label="候选回填与人工审核">
    <h3>候选回填与人工审核</h3>
    {record.template_snapshot?.generated&&record.template_snapshot.quality_policy!=='extractive-qa-v4'&&<p className="rec-warning">建议重新生成，以检查旧版回填内容。</p>}
    <div className="candidate-quality-summary"><button className="btn btn-secondary" aria-pressed={onlyAttention} onClick={()=>setOnlyAttention(v=>!v)}>{onlyAttention?'显示全部候选':'只看需重点核对'} · {latest.filter(attention).length}</button><span>待补充 {missing.length} 项</span></div>
    {!!missing.length&&<details className="rec-analysis-history"><summary>未回填与校验提示 · {missing.length}</summary>{missing.map(f=><article className="candidate-card" key={f.id}><strong>{f.label}</strong><p>{checks.find(x=>x.field_id===f.id&&x.status==='blocked')?.message||'尚无通过校验的回答依据'}</p></article>)}</details>}
    {!!latest.length && <label className="field"><span>本次审核说明（可选）</span><input className="input" maxLength={1000} value={note} disabled={!canReview || busy} onChange={e=>setNote(e.target.value)} placeholder="填写采用或驳回的理由，随下一次操作保存"/></label>}
    {latest.length ? <div className="candidate-grid">{visible.map(card)}{!visible.length&&<p className="rec-muted">暂无需重点核对的候选</p>}</div> : <p className="rec-muted">根据当前文字重新生成后，将在这里显示候选及来源。</p>}
    {!!record.qa_pairs?.length && <details className="rec-analysis-history"><summary>原文问答关联 · {record.qa_pairs.length} 组</summary><p className="rec-muted">仅根据明确的问答文字标记或人工角色标签关联。可能的追问关系待人工核对。</p>{record.qa_pairs.map((pair,i)=><article key={pair.id} className="candidate-card"><strong>第 {i+1} 组{pair.possible_followup_of!==null?` · 可能补充第 ${pair.possible_followup_of+1} 组`:''}</strong><p>问：{pair.question}</p><p>答：{pair.answer || '尚无关联回答'}</p></article>)}</details>}
    {!!history.length && <details className="rec-analysis-history"><summary>候选历史 · {history.length} 条</summary><div className="candidate-grid">{history.map(card)}</div></details>}
    {evidence && <Dialog title="候选原文依据" onClose={()=>setEvidence(null)} className="rec-snapshot-dialog"><p className="rec-muted">来源：生成时保存的文字快照。{evidence.stale?'当前文字已变化，仅查看历史。':'可对照当前文字和原音。'}</p>{evidence.sources.map((source,i)=><section key={i} className="candidate-card"><strong>{source.precision==='exact'?'精确文字范围':'关联段落（需人工核对候选含义）'}</strong><p className="rec-verbatim">{source.quote}</p>{source.audio_id && <button className="btn btn-secondary" onClick={()=>{setEvidence(null);onPlay(source.audio_id!, (source.start_ms || 0)/1000);}}>{source.start_ms!==null?'回听标注位置':'从原音开头回听'}</button>}</section>)}</Dialog>}
  </section>;
}
