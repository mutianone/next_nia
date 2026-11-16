import { useEffect, useState } from 'react';
import { api } from '../api';
import { PageHeading } from '../components/PageHeading';
import { PortraitGraph, type RadialProfile } from '../components/profiles/PortraitGraph';
import type { PeopleResponse } from '../people-types';
import { useViewState } from '../hooks/useWorkspaceView';
import './Profiles.css';
export type { Profile, Fact, Relation } from './LegacyProfiles';
export { errorMessage } from './LegacyProfiles';
export function Profiles({onOpenRecord}:{onOpenRecord?:(id:string)=>void}={}) {
 const [selected,setSelected]=useViewState('portrait.selected','');
 const [query,setQuery]=useState(''),[page,setPage]=useState(1),[list,setList]=useState<PeopleResponse|null>(null),[data,setData]=useState<RadialProfile|null>(null),[error,setError]=useState(''),[reload,setReload]=useState(0),[trail,setTrail]=useState<{id:string;name:string}[]>([]);
 useEffect(()=>{const c=new AbortController();const timer=setTimeout(()=>{setError('');api<PeopleResponse>(`/people?q=${encodeURIComponent(query)}&page=${page}&page_size=20`,{signal:c.signal}).then(setList).catch(e=>{if(!c.signal.aborted)setError(e.message)});},200);return()=>{clearTimeout(timer);c.abort();};},[query,page,reload]);
 useEffect(()=>{if(!selected){setData(null);return;}const c=new AbortController();setData(null);setError('');api<RadialProfile>(`/profiles/${selected}`,{signal:c.signal}).then(setData).catch(e=>{if(!c.signal.aborted)setError(e.message)});return()=>c.abort();},[selected,reload]);
 function open(id:string){if(data)setTrail(v=>[...v,{id:data.person.id,name:data.person.name}]);setSelected(id);}
 return <div className="page-content portrait-page"><PageHeading title="人物画像分析" description="个人信息与关系图谱"><button className="btn btn-secondary" onClick={()=>setReload(v=>v+1)}>刷新</button></PageHeading>{error&&<p role="alert" className="p5-notice error">{error}<button onClick={()=>setReload(v=>v+1)}>重试</button></p>}
 {!selected?<section className="panel portrait-directory"><h2>选择人员</h2><input className="input" aria-label="搜索画像人员" placeholder="搜索姓名、证件号码或档案编号" value={query} onChange={e=>{setQuery(e.target.value);setPage(1);}}/><div className="portrait-people">{list?.items.map(p=><button className="panel" key={p.id} onClick={()=>{setSelected(p.id);setTrail([]);}}><span className="portrait-person-mark">{p.name.slice(0,1)}</span><strong>{p.name}</strong><small>{p.nationality||'国籍未填写'} · {p.document_number?'••••'+p.document_number.slice(-4):'证件未填写'}</small><span>查看画像 →</span></button>)}</div>{list&&!list.items.length&&<p>暂无匹配人员</p>}<div className="p5-pager"><button className="btn" disabled={page===1} onClick={()=>setPage(p=>p-1)}>上一页</button><span>{page} / {Math.max(1,Math.ceil((list?.total||0)/20))}</span><button className="btn" disabled={!list||page*20>=list.total} onClick={()=>setPage(p=>p+1)}>下一页</button></div></section>:<><div className="portrait-breadcrumb"><button className="btn btn-secondary" onClick={()=>{setSelected('');setTrail([]);}}>返回人员列表</button>{trail.map((t,i)=><button key={i} className="text-button" onClick={()=>{setSelected(t.id);setTrail(v=>v.slice(0,i));}}>{t.name} →</button>)}{data&&<strong>{data.person.name}</strong>}</div>{data?<PortraitGraph key={data.person.id} profile={data} onOpen={open} onOpenRecord={onOpenRecord}/>:!error&&<p role="status">正在读取人员画像…</p>}</>}
 </div>;
}
