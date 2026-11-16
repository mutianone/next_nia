import {useEffect,useState} from 'react';
import {api} from '../api';
import {recordError,type RecordDetail} from '../record-types';
type Diff={field_id:string;label?:string;before:string;after:string;locked?:boolean};
type Plan={expected_record_revision:number;expected_template_revision:string;expected_field_revisions:Record<string,number>;expected_candidate_revisions?:Record<string,number>;fill_batch_id?:string;candidate_ids?:string[];differences:Diff[]};
type History={application_id:string;operation:string;applied_by:string;applied_at:string;restore_preview:Plan|null;restore_error:string|null;differences:Diff[];reason:string};
type Pending={path:string;body:object};
export function RecordFillApply({record,batchId,candidates,disabled,onApplied}:{record:RecordDetail;batchId:string;candidates:string[];disabled:boolean;onApplied?:(r:RecordDetail)=>void}){
 const [enabled,setEnabled]=useState(false),[history,setHistory]=useState<History[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [plan,setPlan]=useState<Plan|null>(null),[restoreId,setRestoreId]=useState<string|null>(null),[reason,setReason]=useState(''),[confirmed,setConfirmed]=useState(false),[pending,setPending]=useState<Pending|null>(null);
 const root=`/records/${record.id}`;
 const load=async()=>{const h=await api<{record_structured_fill_apply_enabled:boolean;items:History[]}>(`${root}/fill-apply/history`);setEnabled(h.record_structured_fill_apply_enabled===true);setHistory(h.items??[]);};
 useEffect(()=>{let active=true;void api<{record_structured_fill_apply_enabled:boolean;items:History[]}>(`${root}/fill-apply/history`).then(h=>{if(active){setEnabled(h.record_structured_fill_apply_enabled===true);setHistory(h.items??[]);}}).catch(e=>{if(active)setError(recordError(e));});return()=>{active=false;};},[root,batchId,record.revision]);
 const run=async(fn:()=>Promise<void>)=>{setBusy(true);setError('');try{await fn();}catch(e){setError(recordError(e));if((e as {status?:number}).status===409){setPlan(null);setPending(null);setConfirmed(false);}}finally{setBusy(false);}};
 const submit=async(p:Pending)=>{await api(p.path,{method:'POST',body:JSON.stringify(p.body)});const updated=await api<RecordDetail>(root);await load();setPending(null);setPlan(null);setConfirmed(false);onApplied?.(updated);};
 if(!enabled&&!history.length&&!error)return null;
 return <section aria-label="正式字段应用">
  {error&&<p role="alert">{error}</p>}
  <button type="button" disabled={!enabled||disabled||busy||!!pending||!candidates.length} onClick={()=>void run(async()=>{const p=await api<Plan>(`${root}/fill-apply/preview`,{method:'POST',body:JSON.stringify({fill_batch_id:batchId,candidate_ids:candidates})});setPlan(p);setRestoreId(null);setConfirmed(false);})}>预览正式应用差异</button>
  {plan&&<><table><thead><tr><th>字段</th><th>当前值</th><th>{restoreId?'恢复值':'待应用值'}</th><th>锁定状态</th></tr></thead><tbody>{plan.differences.map(d=><tr key={d.field_id}><td>{d.label??d.field_id}</td><td style={{whiteSpace:'pre-wrap'}}>{d.before||'—'}</td><td style={{whiteSpace:'pre-wrap'}}>{d.after||'—'}</td><td>{d.locked?'已锁定':'未锁定'}</td></tr>)}</tbody></table>
   {restoreId&&<label>恢复原因<input aria-label="恢复原因" value={reason} onChange={e=>setReason(e.target.value)}/></label>}
   <label><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>已核对差异，授权本次{restoreId?'恢复':'正式应用'}</label>
   <button type="button" disabled={!enabled||disabled||busy||!!pending||!confirmed||(!!restoreId&&!reason.trim())} onClick={()=>void run(async()=>{const {differences:_,...revision}=plan;const p={path:restoreId?`${root}/fill-apply/${restoreId}/restore`:`${root}/fill-apply`,body:{...revision,idempotency_key:crypto.randomUUID(),confirmed:true,...(restoreId?{reason}: {})}};setPending(p);await submit(p);})}>{restoreId?'确认恢复字段':'确认应用到正式笔录'}</button>
  </>}
  {pending&&<button type="button" disabled={busy||!enabled} onClick={()=>void run(()=>submit(pending))}>重试同一请求</button>}
  <details><summary>正式应用历史</summary>{history.map(h=><article key={h.application_id}><p>{h.applied_at} · {h.applied_by} · {h.operation==='RESTORE'?'恢复':'应用'}{h.reason&&` · ${h.reason}`}</p>{h.differences.map(d=><p style={{whiteSpace:'pre-wrap'}} key={d.field_id}>{d.field_id}：{d.before||'—'} → {d.after||'—'}</p>)}{h.operation==='APPLY'&&<><button type="button" disabled={!enabled||busy||disabled||!!pending||!h.restore_preview} onClick={()=>{setPlan(h.restore_preview);setRestoreId(h.application_id);setReason('');setConfirmed(false);}}>预览恢复</button>{h.restore_error&&<p>{h.restore_error}</p>}</>}</article>)}</details>
 </section>;
}
