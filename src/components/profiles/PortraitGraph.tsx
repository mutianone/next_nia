import { useMemo, useState, useRef, useEffect, type CSSProperties } from 'react';
import { Background, BaseEdge,  ViewportPortal, type Edge, type EdgeProps, type ReactFlowInstance, useStore, Controls, Handle, Position, ReactFlow, type Node, type NodeProps } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { UserRound, Fingerprint, MapPin, Globe2, Users, ChevronDown } from 'lucide-react';
import './PortraitGraph.css';
import type { Profile } from '../../pages/Profiles';
import { ProfileAnalysis } from './ProfileAnalysis';
import { Dialog } from '../Dialog';
import { formatDateTime } from '../../lib/display';
type Link={id:string;role:string;name:string;target_id:string|null;verified:boolean;sources:{title:string;text:string;record_id?:string}[];candidates:{id:string;name:string;nationality:string;document_hint:string}[]};
export type RadialProfile=Profile&{radial?:{relations:Link[];personal?:Record<string,{value:string;source:string;conflict:boolean;sources:{record_id:string;title:string;text:string;value:string}[]}>}};
type Data={title:string;value:string;center?:boolean;group?:boolean;pending?:boolean;hue?:number;phase?:number;important?:boolean;};
type GraphNode=Node<Data,'portrait'>;
function Circle({data}:NodeProps<GraphNode>){
 const compact=useStore(state=>state.transform[2]<.55);
 const Icon=data.center?UserRound:data.group?Users:data.hue===265?Fingerprint:data.hue===38?MapPin:data.hue===175?Globe2:UserRound;
 return <div style={{'--node-hue':data.hue??210,'--node-phase':`${-(data.phase??0)}s`,'--node-delay':`${Math.min(data.phase??0,1)}s`} as CSSProperties} className={`portrait-entity ${compact?'is-compact':''} ${data.center?'is-center':''} ${data.pending?'is-pending':''} ${['未填写','未单独登记'].includes(data.value)?'is-empty':''}`}>
 <Handle type="target" position={Position.Top} style={{top:'50%',left:'50%'}} isConnectable={false}/>
 <span className="portrait-emblem"><span className="portrait-orbit" aria-hidden="true"/><Icon size={data.center?30:22} aria-hidden="true"/></span>
 <span className="portrait-node-label"><small>{data.title}</small><strong>{data.value}</strong></span>
 <span className="portrait-hover-card"><b>{data.title}</b><span>{data.value}</span></span>
 <Handle type="source" position={Position.Bottom} style={{top:'50%',bottom:'auto',left:'50%'}} isConnectable={false}/></div>
}
function StarEdge(props:EdgeProps){const {sourceX:x,sourceY:y,targetX:tx,targetY:ty}=props;const dx=tx-x,dy=ty-y;const path=`M ${x} ${y} Q ${x+dx*.5-dy*.1} ${y+dy*.5+dx*.1} ${tx} ${ty}`;return <><BaseEdge path={path} style={props.style}/><circle className="portrait-signal" r="2.5" fill="var(--primary)"><animateMotion dur="8s" begin={`${-(Number(props.id.replace(/\D/g,''))%8)}s`} repeatCount="indefinite" path={path}/></circle></>}
const edgeTypes={star:StarEdge};
const nodeTypes={portrait:Circle};
export function PortraitGraph({profile,onOpen,onOpenRecord}:{profile:RadialProfile;onOpen:(id:string)=>void;onOpenRecord?:(id:string)=>void}){
 const canvas=useRef<HTMLDivElement>(null);
 const flow=useRef<ReactFlowInstance<GraphNode>|null>(null);
 function focusNode(id:string|null){const root=canvas.current;if(!root)return;root.classList.toggle('has-focus',!!id);root.querySelectorAll('.react-flow__node').forEach(el=>el.classList.toggle('is-highlighted',el.getAttribute('data-id')===id||el.getAttribute('data-id')==='self'));root.querySelectorAll('.react-flow__edge').forEach(el=>el.classList.toggle('is-highlighted',el.getAttribute('data-testid')==='rf__edge-edge-'+id));}
 const [mode,setMode]=useState<'personal'|'relations'|'analysis'>('personal'),[chosen,setChosen]=useState<string|null>(null),[showEmpty,setShowEmpty]=useState(false);
 useEffect(()=>{if(!canvas.current)return;let timer:ReturnType<typeof setTimeout>;const observer=new ResizeObserver(()=>{clearTimeout(timer);timer=setTimeout(()=>void flow.current?.fitView({padding:.17,maxZoom:1,duration:180}),100);});observer.observe(canvas.current);return()=>{observer.disconnect();clearTimeout(timer);};},[mode]);
 const original=profile.person;
 const resolved=profile.radial?.personal||{};
 const p={...original,...Object.fromEntries(Object.entries(resolved).filter(([,v])=>!v.conflict).map(([k,v])=>[k,v.value]))};
 const relations=profile.radial?.relations||[];
 const labels:Record<string,string>={'姓名':'name','英文姓名':'english_name','性别':'gender','出生日期':'birth_date','来自国家':'nationality','护照号码':'document_number','居住地':'address','联系电话':'phone','入华原因':'purpose','入境时间':'entry_date','出境时间':'exit_date'};
 const groups=useMemo(()=>[
  {title:'身份信息',items:[['姓名',p.name],['英文姓名',p.english_name],['性别',({male:'男',female:'女',unknown:'未填写'})[p.gender]],['出生日期',p.birth_date],['来自国家',p.nationality]]},
  {title:'证件信息',items:[['档案编号',p.code],[p.document_type==='passport'?'护照号码':'证件号码',p.document_number],['身份证号码',profile.facts.find(f=>!f.archived&&f.verified&&['身份证号','身份证号码'].includes(f.label))?.value||'未单独登记'],['证件有效期',p.document_expiry]]},
  {title:'居住与联系',items:[['居住地',p.address],['联系电话',p.phone]]},
  {title:'来华情况',items:[['入华原因',p.purpose],...(!(p.stays||[]).length? [['入境时间',resolved.entry_date?.value||'未填写'],['出境时间',resolved.exit_date?.value||'未填写']]:[]),...(p.stays||[]).flatMap((s,i)=>[[`第 ${i+1} 次入境`,s.entry_date],[`第 ${i+1} 次出境`,s.exit_date]])]},
  {title:'其他个人信息',items:[['备注',p.notes],...profile.facts.filter(f=>!f.archived).map(f=>[f.label,f.value])]},
 ],[profile]);
 const emptyCount=groups.flatMap(g=>g.items).filter(([,v])=>!v||['未填写','未单独登记'].includes(v)).length;
 const {nodes,edges,details,tracks}=useMemo(()=>{
  const nodes:GraphNode[]=[{id:'self',type:'portrait',position:{x:-100,y:-100},data:{title:'当前人员',value:p.name,center:true,hue:220},ariaLabel:'当前人员 '+p.name}];
  const edges:Edge[]=[];
  const details:Record<string,{title:string;items:(string|null|undefined)[][]}>= {};
  if(mode==='personal'){
   const sectors=[{title:'基本身份',hue:210,items:[...groups[0].items,...groups[4].items]},{title:'证件信息',hue:265,items:groups[1].items},{title:'居住联系',hue:38,items:groups[2].items},{title:'出入境信息',hue:175,items:groups[3].items}];
   sectors.forEach((sector,gi)=>{
    const items=sector.items.map((item,i)=>({item,i})).filter(({item:[,v]})=>showEmpty||(v&&!['未填写','未单独登记'].includes(v)));
    items.forEach(({item:[label,value],i},index)=>{const ring=Math.floor(index/4);const count=Math.min(4,items.length-ring*4);const a=(-135+gi*90+(count===1?0:((index%4)/(count-1)-.5)*60))*Math.PI/180;const radius=310+ring*160;const id=`info-${gi}-${i}`;nodes.push({id,type:'portrait',position:{x:Math.cos(a)*radius-90,y:Math.sin(a)*radius-42},data:{title:label||'',value:value||'未填写',hue:sector.hue,phase:gi*.14+index*.07},ariaLabel:(label||'')+'：'+(value||'未填写')});edges.push({id:'edge-'+id,source:'self',target:id,type:'star',style:{stroke:`hsl(${sector.hue} 65% 65% / .35)`}});details[id]={title:sector.title,items:[[label,value]]};});
   });
   return {nodes,edges,details,tracks:[240,390]};
  }
  else relations.forEach((r,i)=>{const a=i/Math.max(1,relations.length)*Math.PI*2-Math.PI/2;const radius=Math.max(310,relations.length*32);nodes.push({id:r.id,type:'portrait',position:{x:Math.cos(a)*radius-90,y:Math.sin(a)*radius-42},data:{title:r.role,value:r.name,pending:!r.verified,group:true,hue:[205,270,165,32,325][i%5],phase:i*.65},ariaLabel:r.role+'：'+r.name});edges.push({id:'edge-'+r.id,source:'self',target:r.id,type:'star',label:r.role,style:{strokeDasharray:r.verified?'0':'5 4'}});});
  return {nodes,edges,details,tracks:[] as number[]};
 },[mode,groups,p.name,relations,showEmpty]);
 const selected=relations.find(r=>r.id===chosen);const detail=chosen?details[chosen]:null;
 function click(id:string){if(id==='self'){setChosen('self');return;}const r=relations.find(r=>r.id===id);if(mode==='relations'&&r?.target_id){onOpen(r.target_id);return;}setChosen(id);}
 return <section className="panel portrait-graph-panel"><div className="portrait-graph-heading"><div role="tablist" aria-label="人物画像类型"><button role="tab" aria-selected={mode==='personal'} onClick={()=>{setMode('personal');setChosen(null);focusNode(null);}}>个人信息画像</button><button role="tab" aria-selected={mode==='relations'} onClick={()=>{setMode('relations');setChosen(null);focusNode(null);}}>关系图谱</button><button role="tab" aria-selected={mode==='analysis'} onClick={()=>{setMode('analysis');setChosen(null);focusNode(null);}}>AI 线索研判</button></div>{mode==='personal'&&<button className="portrait-missing-toggle" aria-pressed={showEmpty} onClick={()=>setShowEmpty(v=>!v)}>待补充信息 <b>{emptyCount}</b><ChevronDown size={14}/></button>}</div>
 {mode==='analysis'?<ProfileAnalysis personId={p.id} name={p.name} onOpenRecord={onOpenRecord}/>:<div className="portrait-canvas portrait-starmap" ref={canvas}><ReactFlow onInit={instance=>{flow.current=instance;}} proOptions={{hideAttribution:true}} key={mode+String(showEmpty)} nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodeMouseEnter={(_,n)=>focusNode(n.id)} onNodeMouseLeave={()=>focusNode(null)} fitView fitViewOptions={{padding:.17,maxZoom:1}} minZoom={.15} maxZoom={2} nodesDraggable={false} nodesConnectable={false} onNodeClick={(_,node)=>click(node.id)} onKeyDown={event=>{if(event.key==='Enter'){const id=(event.target as HTMLElement).closest('.react-flow__node')?.getAttribute('data-id');if(id){event.preventDefault();click(id);}}}}><ViewportPortal><div className="portrait-track-layer" aria-hidden="true">{tracks.map(r=><div className="portrait-track" key={r} style={{width:r*2,height:r*2,left:-r,top:-r}}/>)}</div></ViewportPortal><Background gap={36} size={.6}/><Controls showInteractive={false}/></ReactFlow>{mode==='personal'&&<div className="portrait-legend" aria-label="信息分组">{['基本身份','证件信息','居住联系','出入境信息'].map((label,i)=><span key={label} style={{'--legend-color':['#62b5ff','#b197fc','#ecc080','#67d3bd'][i]} as CSSProperties}><i/>{label}</span>)}</div>}{mode==='relations'&&!relations.length&&<div className="portrait-empty">暂无有明确依据的关系人物</div>}</div>}
 {mode==='personal'&&<details className="portrait-text-view"><summary>查看全部个人信息</summary>{groups.map(g=><section key={g.title}><h3>{g.title}</h3><dl>{g.items.map(([label,value],i)=><div key={i}><dt>{label}</dt><dd>{value||'未填写'}</dd></div>)}</dl></section>)}</details>}
 {chosen&&<Dialog className="portrait-detail-drawer" title={chosen==='self'?p.name:detail?.title||`${selected?.role}：${selected?.name}`} onClose={()=>setChosen(null)}>{chosen==='self'?<p>{p.nationality} · 档案更新时间 {formatDateTime(p.updated_at)}</p>:detail?<dl className="portrait-fields">{detail.items.map(([label,value],i)=><div key={i}><dt>{label}</dt><dd>{value||'未填写'}</dd>{resolved[labels[label||'']]?.source==='record'&&<dd><small>{resolved[labels[label||'']].conflict?'笔录存在不同说法，待核对':'来源：关联笔录（待核对）'}</small>{resolved[labels[label||'']].sources.map((s,j)=><p key={j}>{s.text} {onOpenRecord&&<button className="btn btn-secondary" onClick={()=>onOpenRecord(s.record_id)}>查看来源笔录</button>}</p>)}</dd>}</div>)}</dl>:selected&&<><p>{selected.verified?'已确认关系':'笔录或登记材料提及，尚待确认'}</p>{selected.sources.map((s,i)=><section key={i}><h3>{s.title}</h3><p style={{whiteSpace:'pre-wrap'}}>{s.text}</p>{s.record_id&&onOpenRecord&&<button className="btn btn-secondary" onClick={()=>onOpenRecord(s.record_id!)}>查看来源笔录</button>}</section>)}{selected.candidates.length>0?<><h3>同名档案 · 请核对是否同一人</h3>{selected.candidates.map(c=><button key={c.id} className="btn btn-secondary" onClick={()=>onOpen(c.id)}>{c.name} · {c.nationality} · {c.document_hint} →</button>)}</>:<p>系统内暂无可匹配的人员档案</p>}</>}</Dialog>}
 </section>;
}
