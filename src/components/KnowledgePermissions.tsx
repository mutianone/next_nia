import { useEffect, useState } from 'react';
import { api } from '../api';
import type { KnowledgeList } from '../knowledge-types';
import { errorMessage } from '../knowledge-types';

export function KnowledgePermissions() {
  const [data, setData] = useState<KnowledgeList | null>(null), [page, setPage] = useState(1);
  const [busy, setBusy] = useState(''), [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController(); setData(null); setError('');
    api<KnowledgeList>(`/knowledge?page=${page}&page_size=10`, { signal: controller.signal }).then(result => { if (!Array.isArray(result?.items)) throw new Error('法规资料许可读取失败，请刷新重试。'); setData(result); }).catch(e => { if (!controller.signal.aborted) setError(errorMessage(e)); });
    return () => controller.abort();
  }, [page]);
  async function change(id: string, allowed: boolean) {
    setBusy(id); setError('');
    try { await api(`/knowledge/${id}`, { method: 'PATCH', body: JSON.stringify({ allow_external: allowed }) }); setData(current => current ? {...current, items: current.items.map(item => item.id === id ? {...item, allow_external: allowed} : item)} : current); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(''); }
  }
  return <details className="settings-external-permissions"><summary>法规资料许可</summary><p>新导入资料默认勾选；已有明确关闭的许可保持不变。仅已发布且在适用期内的资料参与问答。此处修改立即保存。</p>{error && <p role="alert">{error}</p>}{data?.items.map(item => <label key={item.id}><input type="checkbox" checked={item.allow_external} disabled={Boolean(busy)} onChange={event => void change(item.id, event.target.checked)}/><span><strong>允许《{item.title}》相关片段用于 AI 问答</strong><small>{item.version} · {item.status === 'published' ? '已发布' : item.status === 'draft' ? '待审核' : '已归档'}</small></span></label>)}{!data && !error && <p>正在读取资料…</p>}{data && !data.total && <p>暂无法规资料。</p>}<div className="calendar-footer"><button type="button" className="btn btn-secondary" disabled={Boolean(busy) || page === 1} onClick={() => setPage(page - 1)}>上一页</button><span>{page} / {Math.max(1,Math.ceil((data?.total || 0)/10))}</span><button type="button" className="btn btn-secondary" disabled={Boolean(busy) || !data || page*10 >= data.total} onClick={() => setPage(page + 1)}>下一页</button></div></details>;
}
