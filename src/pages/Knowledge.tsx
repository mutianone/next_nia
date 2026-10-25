import { Access } from '../auth/AuthContext';
import { EditorWorkspace } from '../components/EditorWorkspace';
import { useListPosition, useViewState } from '../hooks/useWorkspaceView';
import { PageHeading } from '../components/PageHeading';
import { Choice } from '../components/Choice';
import { usePageDirty } from '../hooks/usePageDirty';
import { confirmAction } from '../lib/confirmation';
import { useEffect, useRef, useState } from 'react';
import { Archive, ArrowDownToLine, ArrowRight, BookOpen, Check, CheckCircle2, ChevronLeft, ChevronRight, CircleAlert, FileText, LoaderCircle, LockKeyhole, Plus, Search, ShieldCheck, Upload, X } from 'lucide-react';
import { api } from '../api';
import { Dialog } from '../components/Dialog';
import type { KnowledgeDocument as Doc, KnowledgeList, KnowledgeStatus, SearchResult } from '../knowledge-types';
import { errorMessage, period, statusLabels, today } from '../knowledge-types';

type Metadata = { title: string; version: string; authority: string; doc_number: string; category: string; source: string; series_key: string; published_on: string; effective_from: string; effective_to: string };
const emptyMetadata: Metadata = { title: '', version: '', authority: '', doc_number: '', category: '法律法规', source: '', series_key: '', published_on: '', effective_from: '', effective_to: '' };
const metadataOf = (doc: Doc): Metadata => Object.fromEntries(Object.keys(emptyMetadata).map(key => [key, doc[key as keyof Doc] || ''])) as Metadata;
function MetadataFields({ value, change, disabled }: { value: Metadata; change: (v: Metadata) => void; disabled?: boolean }) {
  const input = (key: keyof Metadata, label: string, options: { required?: boolean; type?: string; placeholder?: string; wide?: boolean } = {}) => <label className={`field ${options.wide ? 'kb-span-all' : ''}`} key={key}><span>{label}{options.required && <i className="required-dot">*</i>}</span><input className="input" type={options.type || 'text'} required={options.required} value={value[key]} maxLength={key === 'version' ? 80 : key === 'doc_number' ? 120 : key === 'source' ? 1000 : 200} onChange={e => change({ ...value, [key]: e.target.value })} placeholder={options.placeholder} disabled={disabled}/></label>;
  return <div className="kb-form-grid">
    {input('title', '资料名称', { required: true, wide: true, placeholder: '填写完整的法规或规范性文件名称' })}
    {input('version', '版本标识', { required: true, placeholder: '例如：2026 年修订版' })}
    <label className="field"><span>资料分类</span><Choice className="select" value={value.category} onChange={e => change({ ...value, category: e.target.value })} disabled={disabled}>{['法律法规', '部门规章', '规范性文件', '裁量标准', '业务指引', '其他资料'].map(c => <option key={c}>{c}</option>)}</Choice></label>
    {input('authority', '发布机关', { placeholder: '以原文件记载为准' })}{input('doc_number', '文号', { placeholder: '无文号可留空' })}
    {input('source', '资料来源', { wide: true, placeholder: '记录官方网站地址或材料提供单位；系统不访问此地址' })}
    {input('published_on', '公布日期', { type: 'date' })}{input('series_key', '版本关联标识', { placeholder: '同一法规使用相同标识；留空按名称关联' })}
    {input('effective_from', '施行日期', { type: 'date' })}{input('effective_to', '效力截止日期（含当天）', { type: 'date' })}
  </div>;
}

export function DocumentViewer({ id, onClose, onChanged, highlight, workspace = false }: { id: string; onClose: () => void; onChanged?: () => void; highlight?: string; workspace?: boolean }) {
  const [doc, setDoc] = useState<Doc | null>(null);
  const [metadata, setMetadata] = useState<Metadata>(emptyMetadata);
  const [allowExternal, setAllowExternal] = useState(false);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [review, setReview] = useState(false);
  const [reviewConfirmed, setReviewConfirmed] = useState(false);
  const [filter, setFilter] = useState('');
  const load = async () => { setError(''); try { const d = await api<Doc>(`/knowledge/${id}`); setDoc(d); setMetadata(metadataOf(d)); setAllowExternal(d.allow_external); } catch (e) { setError(errorMessage(e)); } };
  useEffect(() => { void load(); }, [id]);
  const dirty = !!doc && (JSON.stringify(metadata) !== JSON.stringify(metadataOf(doc)) || allowExternal !== doc.allow_external);
  usePageDirty(dirty || busy);
  useEffect(() => { if (doc && highlight && !editing) document.getElementById(`kb-chunk-${highlight}`)?.scrollIntoView({ block: 'center' }); }, [doc, highlight, editing]);
  const close = async () => { if (busy) return; if (dirty && !(await confirmAction('资料信息尚未保存，确定关闭吗？', {"title":"保留这次修改？","confirmLabel":"放弃修改","cancelLabel":"继续编辑","tone":"warning"}))) return; onClose(); };
  async function save(status?: KnowledgeStatus) {
    if (!doc) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const payload = status === 'archived' ? { status } : { ...metadata, published_on: metadata.published_on || null, effective_from: metadata.effective_from || null, effective_to: metadata.effective_to || null, ...(status ? { status } : {}) };
      const updated = await api<Doc>(`/knowledge/${id}`, { method: 'PATCH', body: JSON.stringify(payload) });
      setDoc(updated); setMetadata(metadataOf(updated)); setAllowExternal(updated.allow_external); setEditing(false); setReview(false); setReviewConfirmed(false); setNotice(status === 'published' ? '资料已发布，可按施行日期检索。' : status === 'archived' ? '资料已归档，已从检索范围移除。' : '资料信息已保存。'); onChanged?.();
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  const chunks = (doc?.chunks || []).filter(c => `${c.article} ${c.text}`.includes(filter));
  const content = <>
    {error && <div className="kb-error" role="alert"><CircleAlert size={17}/>{error}{!doc && <button onClick={load}>重试</button>}</div>}
    {!doc && !error && <div className="kb-empty"><LoaderCircle className="spinning"/><p>正在读取本地原文…</p></div>}
    {doc && <><div className="kb-document-heading"><div className="kb-document-icon"><BookOpen size={26}/></div><div><div className="kb-inline"><span className={`kb-status ${doc.status}`}>{statusLabels[doc.status]}</span><span className="muted">{doc.category} · {doc.version}</span></div><h3>{doc.title}</h3><p>{doc.authority || '发布机关未填写'}{doc.doc_number ? ` · ${doc.doc_number}` : ''}</p></div></div>
      {notice && <div className="kb-success" role="status"><CheckCircle2 size={17}/>{notice}</div>}
      <div className="kb-document-actions"><Access permission="admin"><button className="btn btn-secondary" onClick={() => setEditing(!editing)} disabled={busy}>{editing ? '查看原文' : '编辑资料信息'}</button></Access><Access permission="write"><a className="btn btn-secondary" href={`/api/knowledge/${id}/file`} download><ArrowDownToLine size={15}/>原始文件</a></Access><span className="kb-action-spacer"/>{doc.status !== 'published' ? <Access permission="admin"><button className="btn btn-primary" disabled={busy} onClick={() => { setReview(true); setEditing(true); }}>审核并发布<ArrowRight size={15}/></button></Access> : <Access permission="admin"><button className="btn btn-secondary" disabled={busy} onClick={async () => { if ((await confirmAction('归档后，该版本将不再参与检索与问答，仍可查看原文。确定归档吗？', {"title":"将此记录归档？","confirmLabel":"确认归档","cancelLabel":"暂不归档","tone":"info"}))) void save('archived'); }}><Archive size={15}/>归档版本</button></Access>}</div>
      {editing ? <form onSubmit={e => { e.preventDefault(); void save(review ? 'published' : undefined); }} className="kb-edit-form"><MetadataFields value={metadata} change={setMetadata} disabled={busy}/><p className="kb-consent">资料的 AI 外发许可在“系统配置 → AI 服务 → 法规资料许可”中管理。</p>{review && <div className="kb-review-box"><ShieldCheck size={20}/><div><strong>发布前核对原文、来源与适用日期</strong><p>发布表示完成本地人工审核，不代表系统已核验文件的法律效力。相同法规版本的有效日期不能重叠。</p><label className="kb-checkbox"><input type="checkbox" checked={reviewConfirmed} onChange={e => setReviewConfirmed(e.target.checked)}/>我已核对以上资料及有效日期</label></div></div>}<div className="kb-form-footer"><Access permission="admin"><button type="button" className="btn btn-secondary" disabled={busy} onClick={() => { setMetadata(metadataOf(doc)); setAllowExternal(doc.allow_external); setEditing(false); setReview(false); }}>取消修改</button></Access><Access permission="admin"><button className="btn btn-primary" disabled={busy || (review && !reviewConfirmed)}>{busy ? <LoaderCircle className="spinning" size={16}/> : <Check size={16}/>} {review ? '确认审核并发布' : '保存资料信息'}</button></Access></div></form> : <><dl className="kb-metadata"><div><dt>适用日期</dt><dd>{period(doc.effective_from, doc.effective_to)}</dd></div><div><dt>资料来源</dt><dd>{doc.source || '尚未登记'}</dd></div><div><dt>本地原件</dt><dd>{doc.filename} · {(doc.size_bytes / 1024).toFixed(1)} KB</dd></div><div><dt>AI 使用范围</dt><dd>{doc.allow_external ? '允许相关片段参与 AI 问答' : '仅供本地检索'}</dd></div></dl><div className="kb-text-toolbar"><h4>条款原文 <span>{doc.chunk_count}</span></h4><label className="kb-search-inline"><Search size={15}/><input value={filter} onChange={e => setFilter(e.target.value)} placeholder="在此文件中查找" aria-label="在此文件中查找"/></label></div><div className="kb-chunks">{chunks.map(c => <article className={`kb-chunk ${highlight === c.id ? 'highlighted' : ''}`} key={c.id} id={`kb-chunk-${c.id}`}><div><strong>{c.article || `片段 ${c.ordinal}`}</strong>{c.page && <span>第 {c.page} 页</span>}</div><p>{c.text}</p></article>)}{!chunks.length && <div className="kb-empty"><Search/><p>未找到对应片段</p></div>}</div></>}
    </>}
  </>;
  return workspace ? <EditorWorkspace title="法规原文与版本" backLabel="返回法规列表" onBack={close} className="knowledge-workspace">{content}</EditorWorkspace> : <Dialog title="法规原文与版本" onClose={close} className="kb-document-dialog">{content}</Dialog>;
}

function ImportDialog({ onClose, onImported }: { onClose: () => void; onImported: (id: string) => void }) {
  const [metadata, setMetadata] = useState<Metadata>(emptyMetadata);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  function selectFile(selected?: File) {
    if (!selected) return;
    setError(''); setFile(null);
    if (!/\.(pdf|docx|txt|md)$/i.test(selected.name)) { setError('请选择 PDF、DOCX、TXT 或 Markdown 文件。'); return; }
    if (selected.size > 10 * 1024 * 1024) { setError('单个文件不能超过 10 MB。'); return; }
    setFile(selected); setMetadata(m => ({ ...m, title: m.title || selected.name.replace(/\.[^.]+$/, '') }));
  }
  const dirty = !!file || JSON.stringify(metadata) !== JSON.stringify(emptyMetadata);
  usePageDirty(dirty || busy);
  const close = async () => { if (!busy && (!dirty || (await confirmAction('尚未导入，确定关闭并放弃本次填写吗？', {"title":"保留这次修改？","confirmLabel":"放弃修改","cancelLabel":"继续编辑","tone":"warning"})))) onClose(); };
  async function submit() {
    if (!file) { setError('请先选择本地文件。'); return; }
    setBusy(true); setError('');
    try { const form = new FormData(); form.set('file', file); Object.entries(metadata).forEach(([key, value]) => { if (value.trim()) form.set(key, value.trim()); }); const doc = await api<Doc>('/knowledge/import', { method: 'POST', body: form }); onImported(doc.id); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  return <Dialog title="导入法规资料" onClose={close} className="kb-import-dialog"><form onSubmit={e => { e.preventDefault(); void submit(); }}>
    <div className="kb-import-step"><span className="active">1 选择本地原件</span><ArrowRight size={14}/><span>2 登记版本</span><ArrowRight size={14}/><span>3 审核后发布</span></div>
    <div className={`kb-upload ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`} onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); if (!busy) selectFile(e.dataTransfer.files[0]); }}><input ref={input} type="file" accept=".pdf,.docx,.txt,.md" onChange={e => selectFile(e.target.files?.[0])} disabled={busy} aria-label="选择法规文件"/><span className="kb-upload-icon">{file ? <FileText size={26}/> : <Upload size={26}/>}</span><strong>{file ? file.name : '点击选择文件，或拖放到这里'}</strong><p>{file ? `${(file.size / 1024).toFixed(1)} KB · 文件保存在当前环境数据库` : 'PDF / DOCX / TXT / Markdown · 最大 10 MB'}</p>{file && <button type="button" className="text-button" disabled={busy} onClick={() => { setFile(null); if (input.current) input.current.value = ''; }}>重新选择 <X size={13}/></button>}</div>
    <p className="kb-import-note"><LockKeyhole size={14}/>本地提取文字，不调用外部服务。扫描 PDF 请先完成 OCR；系统会提示无法提取的文件。</p>
    <MetadataFields value={metadata} change={setMetadata} disabled={busy}/>
    {error && <div className="kb-error" role="alert"><CircleAlert size={17}/>{error}</div>}
    <div className="kb-form-footer"><span className="muted">导入后进入待审核，不直接参与问答。</span><button className="btn btn-primary" disabled={busy || !file}>{busy ? <LoaderCircle size={17} className="spinning"/> : <Upload size={17}/>} {busy ? '正在本地解析…' : '导入并查看原文'}</button></div>
  </form></Dialog>;
}

export function Knowledge({ onAsk }: { onAsk: () => void }) {
  const [data, setData] = useState<KnowledgeList | null>(null);
  const [status, setStatus] = useViewState('knowledge.status', '');
  const [query, setQuery] = useViewState('knowledge.query', '');
  const [listQuery, setListQuery] = useViewState('knowledge.listQuery', '');
  const [page, setPage] = useViewState('knowledge.page', 1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [importing, setImporting] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useViewState<'library' | 'search'>('knowledge.tab', 'library');
  const [searchText, setSearchText] = useViewState('knowledge.searchText', '');
  const [asOf, setAsOf] = useViewState('knowledge.asOf', today);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [searchedQuery, setSearchedQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [highlight, setHighlight] = useState<string>();
  const requestId = useRef(0);
  async function load() { const id = ++requestId.current; setLoading(true); setError(''); try { const value = await api<KnowledgeList>(`/knowledge?${new URLSearchParams({ q: listQuery, ...(status ? { status } : {}), page: String(page), page_size: '12' })}`); if (id === requestId.current) { setData(value); const last = Math.max(1, Math.ceil(value.total / 12)); if (page > last) setPage(last); } } catch (e) { if (id === requestId.current) setError(errorMessage(e)); } finally { if (id === requestId.current) setLoading(false); } }
  useEffect(() => { void load(); return () => { requestId.current++; }; }, [status, listQuery, page]);
  async function search() { if (!searchText.trim()) return; setSearching(true); setError(''); setResult(null); setSearchedQuery(searchText.trim()); try { setResult(await api<SearchResult>('/knowledge/search', { method: 'POST', body: JSON.stringify({ query: searchText.trim(), as_of: asOf || null, limit: 10 }) })); } catch (e) { setError(errorMessage(e)); } finally { setSearching(false); } }
  const listView = useListPosition(`knowledge:${JSON.stringify([tab,status,listQuery,page])}`, !selected && !importing, !loading && !searching);
  const openDoc = (id: string, chunk?: string) => { listView.remember(chunk || id); setSelected(id); setHighlight(chunk); };
  if(selected) return <DocumentViewer workspace id={selected} highlight={highlight} onClose={() => setSelected(null)} onChanged={() => { setResult(null); void load(); }}/>;
  return <div className="page-content knowledge-page"><PageHeading title="法规知识中心" description="管理法规版本，按适用日期检索条款并查阅原文。" scene="knowledge"><Access permission="admin"><button className="btn btn-primary" onClick={() => setImporting(true)}><Plus size={17}/>导入法规资料</button></Access></PageHeading>
    <div className="kb-main-grid">
      <aside className="kb-directory" aria-label="法规资料目录"><h2><BookOpen size={17}/>资料目录</h2>
        <nav aria-label="按发布状态浏览">{[
          {value:'',label:'全部资料',count:data?.stats.documents},
          {value:'published',label:'已发布',count:data?.stats.published},
          {value:'draft',label:'待审核',count:data?.stats.drafts},
          {value:'archived',label:'已归档',count:data ? data.stats.documents-data.stats.published-data.stats.drafts : undefined},
        ].map(item=><button key={item.value} aria-pressed={tab==='library'&&status===item.value} onClick={()=>{setTab('library');setStatus(item.value);setPage(1);setError('');}}><span>{item.label}</span><b>{item.count??'—'}</b></button>)}</nav>
        <div className="kb-directory-note"><ShieldCheck size={16}/><p>审核发布后，资料进入检索范围。引用时同时核对版本和施行日期。</p></div>
        <Access permission="write"><button className="text-button kb-ask-link" onClick={onAsk}>前往法规问答<ArrowRight size={14}/></button></Access>
        <details className="kb-engine-note"><summary>检索说明</summary><p>{data?.retrieval.message||'正在读取检索状态…'}</p><span>已提取 {data?.stats.chunks??'—'} 段原文</span></details>
      </aside>
      <section className="panel kb-library"><div className="kb-tabs" role="tablist" aria-label="知识库视图"><button role="tab" aria-selected={tab === 'library'} onClick={() => { setTab('library'); setError(''); }}><BookOpen size={17}/>资料库</button><button role="tab" aria-selected={tab === 'search'} onClick={() => { setTab('search'); setError(''); }}><Search size={17}/>条款检索</button><span className="kb-local-badge kb-storage-label"><span/>本地存储</span></div>
      {tab === 'library' ? <><div className="kb-toolbar"><div className="kb-catalogue-heading"><h2>{({published:'已发布资料',draft:'待审核资料',archived:'已归档资料'} as Record<string,string>)[status]||'全部资料'}<span>{data?.total??'—'}</span></h2><p>点击资料查看原文、来源和版本信息</p></div><form className="kb-search-inline" onSubmit={e => { e.preventDefault(); setListQuery(query); setPage(1); }}><Search size={16}/><input value={query} maxLength={200} onChange={e => setQuery(e.target.value)} placeholder="搜索资料名称、版本…" aria-label="搜索资料"/><button type="submit" aria-label="查找资料"><ArrowRight size={15}/></button></form></div>
        {error && <div className="kb-error" role="alert"><CircleAlert size={17}/>{error}<button onClick={load}>重试</button></div>}
        {loading ? <div className="kb-empty"><LoaderCircle className="spinning"/><p>正在读取本地资料库…</p></div> : !data?.items.length ? <div className="kb-empty large"><span className="kb-empty-symbol"><BookOpen size={34}/></span><h3>{listQuery || status ? '没有符合条件的资料' : '暂无已导入资料'}</h3><p>{listQuery || status ? '调整筛选条件，或导入一份新的资料。' : '导入文件后核对来源与版本，审核发布后即可检索。'}</p>{listQuery||status?<button className="btn btn-secondary" onClick={()=>{setQuery('');setListQuery('');setStatus('');setPage(1);}}>清除筛选</button>:<Access permission="admin"><button className="btn btn-secondary" onClick={() => setImporting(true)}><Upload size={16}/>导入本地文件</button></Access>}</div> : <div className="kb-doc-list">{data.items.map(d => <button className="kb-doc-row" key={d.id} data-work-row={d.id} data-selected={listView.selectedRow===d.id} onClick={() => openDoc(d.id)}><span className="kb-file-icon"><FileText size={23}/><small>{d.file_type?.toUpperCase().replace('.', '') || 'DOC'}</small></span><div className="kb-doc-body"><div className="kb-inline"><span className="kb-doc-category">{d.category || '法规资料'}</span><span className={`kb-status ${d.status}`}>{statusLabels[d.status]}</span></div><h3>{d.title}</h3><span className={`work-next-step ${d.status==='draft'?'pending':''}`}>{d.status==='draft' ? (!d.source?.trim() || !d.effective_from ? `待补充：${[!d.source?.trim() && '资料来源', !d.effective_from && '施行日期'].filter(Boolean).join('、')}；随后核对原文` : '待人工核对原文、来源与版本日期') : d.status==='archived'?'已退出检索范围，原文仍可查阅':'已发布，按适用日期参与检索'}</span><p>{d.version} <span>·</span> {d.authority || '发布机关待登记'}</p><div className="kb-doc-meta"><span>{period(d.effective_from, d.effective_to)}</span><span>{d.chunk_count} 段原文</span><span>{d.allow_external ? '可用于 AI 问答' : '仅本地检索'}</span></div></div><ChevronRight size={18}/></button>)}</div>}
        {data && data.total > 0 && <div className="kb-pagination"><span>共 {data.total} 份 · 第 {page} / {Math.max(1, Math.ceil(data.total / 12))} 页</span><div><button className="icon-button" aria-label="上一页" disabled={page === 1 || loading} onClick={() => setPage(p => p - 1)}><ChevronLeft size={17}/></button><button className="icon-button" aria-label="下一页" disabled={page * 12 >= data.total || loading} onClick={() => setPage(p => p + 1)}><ChevronRight size={17}/></button></div></div>}
      </> : <><form className="kb-search-form" onSubmit={e => { e.preventDefault(); void search(); }}><label className="field"><span>查找条款、关键词或业务问题</span><div className="kb-search-bar"><Search size={19}/><input value={searchText} onChange={e => setSearchText(e.target.value)} maxLength={1000} placeholder="例如：居留证件变更需要哪些材料？" required/><button className="btn btn-primary" disabled={searching}>{searching ? <LoaderCircle className="spinning" size={16}/> : <ArrowRight size={16}/>}检索</button></div></label><div className="kb-search-options"><label>适用日期 <input className="input" type="date" value={asOf} onChange={e => setAsOf(e.target.value)} required/></label><span><LockKeyhole size={13}/>仅本地检索 · 不发送至 AI</span></div></form>{error && <div className="kb-error" role="alert"><CircleAlert size={17}/>{error}</div>}{searching ? <div className="kb-empty"><LoaderCircle className="spinning"/><p>正在匹配条款与版本…</p></div> : result ? <div className="kb-results"><div className="kb-result-summary">“{searchedQuery}”找到 {result.items.length} 条相关片段 <small>{result.as_of}</small></div>{!result.items.length && <div className="kb-empty"><Search size={30}/><h3>没有找到可用依据</h3><p>检查资料是否已审核发布、适用日期是否正确，或尝试更具体的关键词。</p></div>}{result.items.map((s, i) => <button className="kb-result" key={s.id} data-work-row={s.id} data-selected={listView.selectedRow===s.id} onClick={() => openDoc(s.document_id, s.id)}><div className="kb-inline"><span className="kb-citation-index">{i + 1}</span><strong>{s.title}</strong><ChevronRight size={16}/></div><p className="kb-result-caption">{s.version} · {s.article}{s.page ? ` · 第 ${s.page} 页` : ''}</p><p className="kb-result-excerpt">{s.text}</p><div className="kb-result-source">来源：{s.source || '未登记'} · {s.allow_external ? '允许 AI 引用' : '仅本地检索'}</div></button>)}</div> : <div className="kb-empty large"><span className="kb-empty-symbol"><Search size={34}/></span><h3>检索法规条款</h3><p>只检索已发布、在所选日期内有效的资料。引用可点击回到完整原文。</p></div>}</>}
    </section></div>
    {importing && <ImportDialog onClose={() => setImporting(false)} onImported={id => { setImporting(false); openDoc(id); void load(); }}/>}
  </div>;
}
