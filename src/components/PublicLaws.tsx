import { useEffect, useState } from 'react';

type Entry = { id:string; title:string; version:string; url:string; effective_from:string; articles:number; fetched_at:string|null; error:string|null };
export function PublicLaws({enabled,disabled,onChange}:{enabled:boolean;disabled:boolean;onChange:(value:boolean)=>void}) {
  const [items,setItems] = useState<Entry[]>([]);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  async function load(refresh=false) {
    setBusy(true);setError('');
    try {
      const response=await fetch('/api/settings/public-laws'+(refresh?'/refresh':''),{method:refresh?'POST':'GET'});
      const data=await response.json();
      if(!response.ok) throw new Error(data.detail || '读取失败，请重试。');
      setItems(data.items);
    } catch(e) {setError(e instanceof Error?e.message:'请求失败，请重试。');}
    finally {setBusy(false);}
  }
  useEffect(()=>{void load()},[]);
  return <section className="panel settings-card">
    <div className="settings-card-heading"><h2>官方法规补充检索</h2><button type="button" className="btn btn-secondary" disabled={busy||disabled||!enabled} onClick={()=>void load(true)}>{busy?'正在读取…':'更新法规缓存'}</button></div>
    <div className="settings-card-body">
      <label style={{display:'flex',alignItems:'center',gap:8}}><input type="checkbox" checked={enabled} disabled={disabled} onChange={e=>onChange(e.target.checked)}/>本地无匹配依据时，允许联网获取官方法规</label>
      <p>更改开关后请保存配置。仅访问下列官方全文地址，问题和业务信息不发送到公开网站。缓存超过 24 小时后按需更新，独立保存，不随生成资料清理。</p>
      {error && <p role="alert">{error}</p>}
      {items.map(item=><div key={item.id} style={{padding:'10px 0',borderTop:'1px solid var(--border)'}}><a href={item.url} target="_blank" rel="noreferrer">{item.title}</a><p style={{margin:'4px 0',fontSize:'0.85em'}}>{item.version} · 本版本自 {item.effective_from} 施行 · {item.articles} 条 · {item.fetched_at?`获取于 ${new Date(item.fetched_at).toLocaleString()}`:'尚未获取'}</p>{item.error && <p role="status">{item.error}</p>}</div>)}
      <small>仅覆盖上述目录，不是全网检索。更新失败的来源暂停使用；历史日期早于所收录版本时不引用该版本。</small>
    </div>
  </section>;
}
