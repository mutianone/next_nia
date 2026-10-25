import { useEffect, useState } from 'react';
import { FileSearch, LoaderCircle, Plus, Sparkles } from 'lucide-react';
import { api } from '../../api';
import { Dialog } from '../Dialog';

type Candidate = { analysis_id: string; fact_index: number; record_title: string; record_revision: number; provider: string; model: string; label: string; value: string; imported: boolean; sources: {segment_id: string; text: string}[] };
export function ProfileCandidates({ personId, onClose, onChanged }: {personId: string; onClose: () => void; onChanged: () => void}) {
  const [items, setItems] = useState<Candidate[]>([]), [loading, setLoading] = useState(true), [saving, setSaving] = useState(''), [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    api<{items: Candidate[]}>(`/profiles/${personId}/analysis-facts`, {signal: controller.signal}).then(data => setItems(data.items)).catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : '读取失败'); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [personId]);
  async function register(item: Candidate) {
    if (saving) return;
    setSaving(`${item.analysis_id}:${item.fact_index}`); setError('');
    try {
      await api(`/profiles/${personId}/facts/from-analysis`, {method: 'POST', body: JSON.stringify({analysis_id: item.analysis_id, fact_index: item.fact_index})});
      setItems(current => current.map(value => value.analysis_id === item.analysis_id && value.fact_index === item.fact_index ? {...value, imported: true} : value));
      onChanged();
    } catch (e) { setError(e instanceof Error ? e.message : '登记失败'); } finally { setSaving(''); }
  }
  return <Dialog title="从笔录提取事实" onClose={() => { if (!saving) onClose(); }} className="rec-confirm-dialog">
    <p className="p5-caption">读取关联笔录中最新有效的 AI 要素，保留原文来源。登记后为“待核验”，可在事实标签中继续编辑、分类和核实。</p>
    {error && <p className="kb-error" role="alert">{error}</p>}
    {loading ? <p role="status"><LoaderCircle className="spinning" size={18}/> 正在读取笔录要素…</p> : !items.length ? <div className="p5-empty"><FileSearch size={30}/><strong>暂无可登记的笔录要素</strong><p>先在智能笔录中关联本人员，保存正文并通过 AI 生成摘要与要素。历史文字整理、已过期摘要和归档笔录不会作为候选。</p></div> : <div className="p5-facts">{items.map(item => <article className="p5-fact" key={`${item.analysis_id}:${item.fact_index}`}><header><span className="badge"><Sparkles size={12}/> 笔录提取</span><span className="p5-status">{item.imported ? '已登记' : '待核验'}</span></header><h3>{item.label}</h3><p>{item.value}</p><small>{item.record_title} · 版本 {item.record_revision} · {item.provider === 'deepseek' ? 'DeepSeek' : '硅基流动'}</small><details><summary>核对原文来源</summary>{item.sources.map(source => <p key={source.segment_id}>{source.text}</p>)}</details><footer><button className="btn btn-secondary" disabled={item.imported || !!saving} onClick={() => void register(item)}>{saving === `${item.analysis_id}:${item.fact_index}` ? <LoaderCircle size={14} className="spinning"/> : <Plus size={14}/>} {item.imported ? '已登记为事实' : '登记为待核验事实'}</button></footer></article>)}</div>}
  </Dialog>;
}
