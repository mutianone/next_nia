import type { Profile } from '../../pages/Profiles';
import { formatDateTime } from '../../lib/display';
export function ProfileOverview({profile,onEvidence,onReview,onRecords}:{profile:Profile;onEvidence:(v:{title:string;text:string})=>void;onReview:()=>void;onRecords:()=>void}) {
 const p=profile.person;
 const days=profile.insights?.document.days_remaining;
 const pending=profile.facts.filter(f=>!f.archived&&!f.verified);
 const facts=profile.facts.filter(f=>!f.archived&&f.verified);
 const notice=days==null?'证件有效期未填写':days<0?`证件已逾期 ${-days} 天`:days===0?'证件今日到期':days<=3?`证件还有 ${days} 天到期`:'';
 const fields=[['证件号码',p.document_number? '••••'+p.document_number.slice(-4):''],['证件有效期',p.document_expiry],['出生日期',p.birth_date],['联系电话',p.phone],['现住址',p.address],['来华事由',p.purpose]];
 return <div className="portrait-overview">
 <section className={`panel portrait-notice ${notice?'attention':''}`}><h2>当前提醒</h2><p>{notice||'当前暂无证件期限提醒'}</p>{pending.length>0&&<button className="text-button" onClick={onReview}>{pending.length} 项信息待核对 →</button>}{profile.timeline.filter(t=>t.kind==='alert'&&t.status!=='closed').map(t=><button key={t.id} className="text-button" onClick={()=>onEvidence({title:t.title,text:t.detail})}>查看预警记录 →</button>)}</section>
 <div className="portrait-columns"><section className="panel"><div className="section-title"><h2>人员情况摘要</h2><button className="text-button" onClick={onRecords}>关联笔录 →</button></div>{p.purpose&&<p>来华事由：{p.purpose}</p>}{['identity','purpose','residence','other'].map(kind=>{const rows=facts.filter(f=>f.kind===kind);return rows.length>0&&<div className="portrait-facts" key={kind}><h3>{{identity:'基本情况',purpose:'来华情况',residence:'居住情况',other:'其他已确认信息'}[kind]}</h3>{rows.map(f=><article key={f.id}><strong>{f.label}</strong><p>{f.value}</p><button className="text-button" onClick={()=>onEvidence({title:f.label,text:`${f.source}\n${f.review_note}\n${JSON.stringify(f.source_snapshot,null,2)}`})}>查看来源</button></article>)}</div>})}{!facts.length&&<p className="p5-empty">暂无已确认的笔录事实</p>}</section>
 <section className="panel"><h2>基本信息</h2><dl className="portrait-fields">{fields.map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value||'未填写'}</dd></div>)}</dl><small>更新于 {formatDateTime(p.updated_at)}</small><button className="text-button" onClick={()=>onEvidence({title:'档案资料来源',text:p.source||'未填写'})}>查看档案来源</button></section></div>
 </div>;
}
