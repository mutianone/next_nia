import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Archive, ArrowDownToLine, ArrowRight, ArrowUpFromLine, CalendarDays, Check, CheckCircle2, ChevronLeft, ChevronRight, CircleAlert, Clock3, FileCheck2, FileSpreadsheet, FileText, Fingerprint, Globe2, LoaderCircle, Pencil, Plus, RefreshCw, Search, ShieldCheck, UserRound, UserRoundCheck, Users, X } from 'lucide-react';
import { api } from '../api';
import { Dialog } from '../components/Dialog';
import type { ImportPreview, PeopleResponse, Person, PersonInput, Stay, StayInput } from '../people-types';
import '../people.css';

const emptyPerson = (): PersonInput => ({ name: '', english_name: '', nationality: '', gender: 'unknown', birth_date: null, document_type: 'passport', document_number: '', document_expiry: null, phone: '', address: '', purpose: '', source: '', verification: 'pending', notes: '' });
const emptyStay = (): StayInput => ({ entry_date: '', permitted_until: null, exit_date: null, permit_type: '', permit_number: '', purpose: '', source: '', notes: '' });
const personFields = (person: Person): PersonInput => Object.fromEntries(Object.keys(emptyPerson()).map(key => [key, person[key as keyof PersonInput] ?? emptyPerson()[key as keyof PersonInput]])) as PersonInput;
const stayFields = (stay: Stay): StayInput => Object.fromEntries(Object.keys(emptyStay()).map(key => [key, stay[key as keyof StayInput] ?? emptyStay()[key as keyof StayInput]])) as StayInput;
const dateLabel = (value: string | null | undefined) => value ? value.slice(0, 10).replaceAll('-', '.') : '未填写';
const timeLabel = (value: string) => { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); };
const messageOf = (error: unknown) => error instanceof Error ? /fetch|network/i.test(error.message) ? '无法连接本地服务，请检查网络与后端运行状态后重试。' : error.message : '操作未完成，请重试。';
const initials = (person: Pick<Person, 'name'>) => [...person.name.trim()].slice(0, 1).join('') || '人';

function Verification({ value }: { value: PersonInput['verification'] }) { return <span className={`people-verification ${value}`}>{value === 'verified' ? <ShieldCheck size={12} /> : <Clock3 size={12} />}{value === 'verified' ? '已核实' : '待核实'}</span>; }
function Field({ label, value, onChange, required, type = 'text', placeholder, full = false, maxLength }: { label: string; value: string | null; onChange: (value: string) => void; required?: boolean; type?: string; placeholder?: string; full?: boolean; maxLength?: number }) {
  return <label className={`field people-field ${full ? 'people-field-full' : ''}`}><span>{label}{required && <b> *</b>}</span><input className="input" type={type} value={value || ''} onChange={event => onChange(event.target.value)} required={required} placeholder={placeholder} maxLength={maxLength} /></label>;
}
function ErrorNotice({ message, retry }: { message: string; retry?: () => void }) { return <div className="people-error" role="alert"><CircleAlert size={18} /><span>{message}</span>{retry && <button className="btn btn-ghost" type="button" onClick={retry}><RefreshCw size={14} />重试</button>}</div>; }
function InfoPair({ label, value, wide = false }: { label: string; value: string | null | undefined; wide?: boolean }) { return <div className={wide ? 'people-info-wide' : ''}><dt>{label}</dt><dd>{value || '未填写'}</dd></div>; }

export function People() {
  const [data, setData] = useState<PeopleResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [archived, setArchived] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [reload, setReload] = useState(0);
  const [notice, setNotice] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Person | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [detailReload, setDetailReload] = useState(0);
  const [editor, setEditor] = useState<{ id: string | null; initial: PersonInput } | null>(null);
  const [form, setForm] = useState<PersonInput>(emptyPerson);
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);
  const [stayEditor, setStayEditor] = useState<{ personId: string; id: string | null; initial: StayInput } | null>(null);
  const [stayForm, setStayForm] = useState<StayInput>(emptyStay);
  const [stayError, setStayError] = useState('');
  const [staySaving, setStaySaving] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState<Person | null>(null);
  const [archiveError, setArchiveError] = useState('');
  const [archiving, setArchiving] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [csvText, setCsvText] = useState('');
  const [fileName, setFileName] = useState('');
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [importError, setImportError] = useState('');
  const [importing, setImporting] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const previewVersion = useRef(0);

  useEffect(() => { const timer = window.setTimeout(() => { setSearch(query.trim()); setPage(1); }, 350); return () => window.clearTimeout(timer); }, [query]);
  useEffect(() => {
    let cancelled = false;
    setLoading(true); setListError('');
    const params = new URLSearchParams({ q: search, archived: String(archived), page: String(page), page_size: String(pageSize) });
    api<PeopleResponse>(`/people?${params}`).then(result => {
      if (cancelled) return;
      if (!result || !Array.isArray(result.items) || !result.stats) throw new Error('人员列表响应不完整，请检查服务版本。');
      if (page > 1 && !result.items.length && result.total > 0) { setPage(Math.max(1, Math.ceil(result.total / pageSize))); return; }
      if (page > 1 && result.total === 0) { setPage(1); return; }
      setData(result);
    }).catch(error => { if (!cancelled) { setListError(messageOf(error)); setData(null); } }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [search, archived, page, pageSize, reload]);
  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    let cancelled = false;
    setDetailLoading(true); setDetailError(''); setDetail(null);
    api<Person>(`/people/${encodeURIComponent(selectedId)}`).then(result => { if (!cancelled) { if (!result?.id) throw new Error('人员详情响应不完整。'); setDetail(result); } })
      .catch(error => { if (!cancelled) setDetailError(messageOf(error)); }).finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [selectedId, detailReload]);
  const formDirty = Boolean(editor && JSON.stringify(form) !== JSON.stringify(editor.initial));
  const stayDirty = Boolean(stayEditor && JSON.stringify(stayForm) !== JSON.stringify(stayEditor.initial));
  const pageDirty = formDirty || stayDirty || saving || staySaving || archiving || importing || Boolean(importOpen && (csvText || previewLoading));
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('settings-dirty-change', { detail: { dirty: pageDirty } }));
    return () => { window.dispatchEvent(new CustomEvent('settings-dirty-change', { detail: { dirty: false } })); };
  }, [pageDirty]);
  useEffect(() => { const guard = (event: BeforeUnloadEvent) => { if (pageDirty) { event.preventDefault(); event.returnValue = ''; } }; window.addEventListener('beforeunload', guard); return () => window.removeEventListener('beforeunload', guard); }, [pageDirty]);

  const refresh = () => setReload(value => value + 1);
  function openEditor(person?: Person) { const next = person ? personFields(person) : emptyPerson(); setForm(next); setEditor({ id: person?.id || null, initial: next }); setFormError(''); }
  function closeEditor() { if (saving) return; if (formDirty && !window.confirm('人员信息尚未保存，确定放弃此次修改吗？')) return; setEditor(null); }
  function openStay(stay?: Stay) { if (!detail) return; const next = stay ? stayFields(stay) : emptyStay(); setStayForm(next); setStayEditor({ personId: detail.id, id: stay?.id || null, initial: next }); setStayError(''); }
  function closeStay() { if (staySaving) return; if (stayDirty && !window.confirm('停留记录尚未保存，确定放弃此次修改吗？')) return; setStayEditor(null); }
  function changePerson<K extends keyof PersonInput>(key: K, value: PersonInput[K]) { setForm(current => ({ ...current, [key]: value })); setFormError(''); }
  function changeStay<K extends keyof StayInput>(key: K, value: StayInput[K]) { setStayForm(current => ({ ...current, [key]: value })); setStayError(''); }
  async function savePerson(event: FormEvent) {
    event.preventDefault(); if (!editor || saving) return;
    if (![form.name, form.nationality, form.document_number, form.source].every(value => value.trim())) { setFormError('请完整填写姓名、国籍、证件号码与信息来源。'); return; }
    setSaving(true); setFormError('');
    try {
      const result = await api<Person>(editor.id ? `/people/${encodeURIComponent(editor.id)}` : '/people', { method: editor.id ? 'PUT' : 'POST', body: JSON.stringify(form) });
      if (!result?.id) throw new Error('保存响应不完整，请刷新列表确认保存状态。');
      setNotice(editor.id ? '人员档案已更新。' : '人员档案已创建。'); setEditor(null); setSelectedId(result.id); setDetailReload(value => value + 1); refresh();
    } catch (error) { setFormError(messageOf(error)); } finally { setSaving(false); }
  }
  async function saveStay(event: FormEvent) {
    event.preventDefault(); if (!stayEditor || staySaving) return;
    if (!stayForm.entry_date || !stayForm.source.trim()) { setStayError('请填写入境日期与信息来源。'); return; }
    if (stayForm.exit_date && stayForm.exit_date < stayForm.entry_date) { setStayError('离境日期不能早于入境日期。'); return; }
    setStaySaving(true); setStayError('');
    try {
      const result = await api<Stay>(`/people/${encodeURIComponent(stayEditor.personId)}/stays${stayEditor.id ? `/${encodeURIComponent(stayEditor.id)}` : ''}`, { method: stayEditor.id ? 'PUT' : 'POST', body: JSON.stringify(stayForm) });
      if (!result?.id) throw new Error('保存响应不完整，请刷新详情确认记录状态。');
      setNotice(stayEditor.id ? '停留记录已更新。' : '停留记录已添加。'); setStayEditor(null); setDetailReload(value => value + 1); refresh();
    } catch (error) { setStayError(messageOf(error)); } finally { setStaySaving(false); }
  }
  async function changeArchive() {
    if (!archiveTarget || archiving) return;
    setArchiving(true); setArchiveError('');
    try {
      const result = await api<Person>(`/people/${encodeURIComponent(archiveTarget.id)}/archive`, { method: 'POST', body: JSON.stringify({ archived: !archiveTarget.archived }) });
      if (!result?.id) throw new Error('服务响应不完整，请刷新确认归档状态。');
      setNotice(archiveTarget.archived ? '档案已恢复至在用列表。' : '档案已归档，可在已归档列表恢复。'); setArchiveTarget(null); setSelectedId(null); refresh();
    } catch (error) { setArchiveError(messageOf(error)); } finally { setArchiving(false); }
  }
  function openImport() { previewVersion.current += 1; setImportOpen(true); setCsvText(''); setFileName(''); setPreview(null); setImportError(''); }
  function closeImport() { if (importing) return; previewVersion.current += 1; setImportOpen(false); setCsvText(''); setPreview(null); setPreviewLoading(false); }
  async function readCsv(file: File | undefined) {
    if (!file) return;
    const version = ++previewVersion.current;
    setPreview(null); setCsvText(''); setImportError(''); setFileName(file.name); setPreviewLoading(false);
    if (!file.name.toLowerCase().endsWith('.csv')) { setImportError('请选择 CSV 文件，可先下载标准模板填写。'); return; }
    if (file.size > 1024 * 1024) { setImportError('文件超过 1 MiB，请分批导入，每批不超过 500 行。'); return; }
    setPreviewLoading(true);
    try {
      const text = await file.text();
      if (version !== previewVersion.current) return;
      if (!text.trim()) throw new Error('文件内容为空，请检查后重新选择。');
      if (text.includes('\uFFFD')) throw new Error('文件编码无法识别，请另存为 UTF-8 编码的 CSV 后重试。');
      setCsvText(text);
      const result = await api<ImportPreview>('/people/import/preview', { method: 'POST', body: JSON.stringify({ csv_text: text }) });
      if (version !== previewVersion.current) return;
      if (!result || !Array.isArray(result.rows) || !Array.isArray(result.errors)) throw new Error('预览响应不完整，请重试。');
      setPreview(result);
    } catch (error) { if (version === previewVersion.current) setImportError(messageOf(error)); }
    finally { if (version === previewVersion.current) setPreviewLoading(false); }
  }
  async function confirmImport() {
    if (!preview || preview.errors.length || !preview.valid_count || !csvText || importing) return;
    setImporting(true); setImportError('');
    try {
      const result = await api<{ imported: number }>('/people/import', { method: 'POST', body: JSON.stringify({ csv_text: csvText }) });
      if (typeof result?.imported !== 'number') throw new Error('导入响应不完整，请刷新档案列表确认。');
      setNotice(`成功导入 ${result.imported} 条人员档案。`); setImportOpen(false); setCsvText(''); setPreview(null); setPage(1); refresh();
    } catch (error) { setImportError(messageOf(error)); } finally { setImporting(false); }
  }
  const exportQuery = new URLSearchParams({ q: query.trim(), archived: String(archived) });
  const totalPages = Math.max(1, Math.ceil((data?.total || 0) / pageSize));
  const stats = [
    { label: '全部档案', value: data?.stats.total, icon: Users, detail: '包含已归档档案', tone: 'primary' },
    { label: '已核实', value: data?.stats.verified, icon: UserRoundCheck, detail: '在用档案 · 人工核实', tone: 'success' },
    { label: '待核实', value: data?.stats.pending, icon: FileCheck2, detail: '在用档案 · 待补充核实', tone: 'warning' },
    { label: '已归档', value: data?.stats.archived, icon: Archive, detail: '保留记录 · 随时恢复', tone: 'muted' },
  ];

  return <div className="page-content people-page">
    <header className="page-heading people-heading"><div><div className="eyebrow"><span className="small-line" />PERSONNEL ARCHIVES</div><h1 className="page-title">人员档案<span className="title-dot">.</span></h1><p className="page-description">归集身份与停留事实，让每一条信息都有来源。</p></div><button className="btn btn-primary people-create" onClick={() => openEditor()}><Plus size={17} />新建人员档案</button></header>
    {notice && <div className="people-notice" role="status"><CheckCircle2 size={17} /><span>{notice}</span><button aria-label="关闭提示" onClick={() => setNotice('')}><X size={16} /></button></div>}
    <div className="people-stats">{stats.map(stat => <div className={`people-stat ${stat.tone}`} key={stat.label}><div><span>{stat.label}</span><stat.icon size={17} /></div><strong>{stat.value === undefined ? '—' : stat.value.toLocaleString()}</strong><small>{stat.detail}</small></div>)}</div>
    <section className="panel people-register">
      <div className="people-register-heading"><div><span className="people-section-kicker">LOCAL REGISTER</span><h2>档案登记册<span>{data ? data.total.toLocaleString() : '—'}</span></h2></div><div className="people-register-tools"><a className="btn btn-ghost" href="/api/people/template.csv" download><FileSpreadsheet size={15} />模板</a><button className="btn btn-secondary" onClick={openImport}><ArrowUpFromLine size={15} />导入</button><a className="btn btn-secondary" href={`/api/people/export.csv?${exportQuery}`} download><ArrowDownToLine size={15} />导出</a></div></div>
      <div className="people-toolbar"><div className="people-search"><Search size={17} /><input value={query} maxLength={200} onChange={event => setQuery(event.target.value)} placeholder="搜索姓名、证件号、国籍…" aria-label="搜索人员档案" />{query && <button aria-label="清空搜索" onClick={() => setQuery('')}><X size={14} /></button>}</div><div className="people-filter-tabs" role="group" aria-label="归档状态"><button aria-pressed={!archived} className={!archived ? 'active' : ''} onClick={() => { setArchived(false); setPage(1); }}>在用档案</button><button aria-pressed={archived} className={archived ? 'active' : ''} onClick={() => { setArchived(true); setPage(1); }}>已归档</button></div><button className="people-refresh icon-button" aria-label="刷新人员列表" onClick={refresh} disabled={loading}><RefreshCw size={17} className={loading ? 'people-spin' : ''} /></button></div>
      <div className="people-register-body" aria-busy={loading}>
        {listError ? <div className="people-list-error"><ErrorNotice message={listError} retry={refresh} /></div> : loading && !data ? <div className="people-state" role="status"><LoaderCircle size={26} className="people-spin" /><h3>正在读取人员档案</h3><p>从当前环境的本地数据库读取记录。</p></div> : data && data.items.length ? <>
          {loading && <div className="people-refreshing" role="status">正在更新列表…</div>}
          <div className="people-table-wrap"><table className="people-table"><thead><tr><th>人员信息</th><th>国籍</th><th>证件信息</th><th>核实状态</th><th>信息来源</th><th>最后更新</th><th><span className="people-sr-only">操作</span></th></tr></thead><tbody>{data.items.map(person => <tr key={person.id} onClick={() => setSelectedId(person.id)}><td><div className="people-person-cell"><span className="people-avatar">{initials(person)}</span><div><button className="people-name-button" onClick={event => { event.stopPropagation(); setSelectedId(person.id); }}>{person.name}</button><small>{person.english_name || person.code}</small></div></div></td><td><span className="people-nationality"><Globe2 size={12} />{person.nationality}</span></td><td><span className="people-document-no">{person.document_number}</span><small className="people-cell-sub">{person.document_type === 'passport' ? '护照' : '其他证件'}</small></td><td><Verification value={person.verification} /></td><td><span className="people-source-cell" title={person.source}>{person.source}</span></td><td className="people-date-cell">{dateLabel(person.updated_at)}</td><td><button className="icon-button" aria-label={`查看${person.name}的档案`} onClick={event => { event.stopPropagation(); setSelectedId(person.id); }}><ChevronRight size={17} /></button></td></tr>)}</tbody></table></div>
          <div className="people-mobile-list">{data.items.map(person => <article className="people-mobile-card" key={person.id}><div className="people-mobile-card-top"><span className="people-avatar">{initials(person)}</span><div><button className="people-name-button" onClick={() => setSelectedId(person.id)}>{person.name}</button><small>{person.english_name || person.code}</small></div><Verification value={person.verification} /></div><dl><div><dt>国籍</dt><dd>{person.nationality}</dd></div><div><dt>{person.document_type === 'passport' ? '护照号码' : '证件号码'}</dt><dd>{person.document_number}</dd></div></dl><div className="people-mobile-card-footer"><span>{person.source}</span><button onClick={() => setSelectedId(person.id)}>查看档案<ArrowRight size={14} /></button></div></article>)}</div>
        </> : !loading && <div className="people-state people-empty"><span className="people-empty-icon"><Users size={30} /></span><span className="people-empty-orbit" /><h3>{search ? '没有找到匹配的档案' : archived ? '暂无已归档人员' : '从第一份人员档案开始'}</h3><p>{search ? '试试其他姓名、国籍或证件号码。' : archived ? '归档后的人员资料会保留在这里，并可随时恢复。' : '新建档案或导入 CSV，建立可追溯的人员信息登记册。'}</p>{search ? <button className="btn btn-secondary" onClick={() => setQuery('')}>清空搜索</button> : !archived && <div className="people-empty-actions"><button className="btn btn-primary" onClick={() => openEditor()}><Plus size={15} />新建档案</button><button className="btn btn-secondary" onClick={openImport}><ArrowUpFromLine size={15} />批量导入</button></div>}</div>}
      </div>
      <footer className="people-pagination"><span>{data ? `共 ${data.total} 条${archived ? '已归档' : '在用'}记录` : '等待数据连接'}{search && <small> · 已应用搜索筛选</small>}</span><div><label>每页<select value={pageSize} onChange={event => { setPageSize(Number(event.target.value)); setPage(1); }} aria-label="每页记录数"><option value={20}>20</option><option value={50}>50</option></select>条</label><div className="people-page-switch"><button className="icon-button" aria-label="上一页" disabled={page <= 1 || loading} onClick={() => setPage(value => value - 1)}><ChevronLeft size={16} /></button><span>{page}<i>/</i>{totalPages}</span><button className="icon-button" aria-label="下一页" disabled={page >= totalPages || loading} onClick={() => setPage(value => value + 1)}><ChevronRight size={16} /></button></div></div></footer>
    </section>
    <div className="people-bottom-note"><ShieldCheck size={14} /><span>统计来自当前数据库。核实状态由工作人员维护，停留期限缺失时不推断违法。</span><span>LOCAL DATA · TRACEABLE FACTS</span></div>

    {selectedId && !editor && !stayEditor && !archiveTarget && <Dialog title="人员档案" onClose={() => setSelectedId(null)} className="people-detail-dialog">
      {detailLoading ? <div className="people-state"><LoaderCircle size={24} className="people-spin" /><p>正在读取档案详情…</p></div> : detailError ? <ErrorNotice message={detailError} retry={() => setDetailReload(value => value + 1)} /> : detail && <>
        <div className="people-detail-hero"><span className="people-avatar large">{initials(detail)}</span><div><div className="people-detail-title"><h2>{detail.name}</h2><Verification value={detail.verification} />{detail.archived && <span className="people-archived-badge">已归档</span>}</div>{detail.english_name && <p>{detail.english_name}</p>}<span><Globe2 size={12} />{detail.nationality}<i />{detail.code}</span></div></div>
        <div className="people-detail-actions"><button className="btn btn-primary" onClick={() => openEditor(detail)}><Pencil size={14} />编辑档案</button><button className="btn btn-secondary" onClick={() => { setArchiveTarget(detail); setArchiveError(''); }}><Archive size={14} />{detail.archived ? '恢复档案' : '归档'}</button></div>
        <section className="people-detail-section"><h3><UserRound size={16} />基本信息</h3><dl className="people-info-grid"><InfoPair label="性别" value={{ unknown: '未知 / 未填写', male: '男', female: '女' }[detail.gender]} /><InfoPair label="出生日期" value={dateLabel(detail.birth_date)} /><InfoPair label="联系电话" value={detail.phone} /><InfoPair label="来华目的" value={detail.purpose} /><InfoPair label="联系地址" value={detail.address} wide /></dl></section>
        <section className="people-detail-section"><h3><Fingerprint size={16} />证件信息</h3><div className="people-document-card"><div><span>{detail.document_type === 'passport' ? 'PASSPORT / 护照' : 'IDENTITY / 其他证件'}</span><Fingerprint size={23} /></div><strong>{detail.document_number}</strong><div><span>证件有效期至</span><b>{dateLabel(detail.document_expiry)}</b></div></div></section>
        <section className="people-detail-section"><div className="people-detail-section-title"><h3><CalendarDays size={16} />停留时间线<span>{detail.stays?.length || 0}</span></h3><button className="btn btn-ghost" onClick={() => openStay()}><Plus size={14} />添加记录</button></div>{detail.stays?.length ? <div className="people-stay-timeline">{[...detail.stays].sort((a, b) => b.entry_date.localeCompare(a.entry_date)).map(stay => <article className="people-stay-item" key={stay.id}><span className="people-timeline-dot" /><div className="people-stay-title"><strong>{dateLabel(stay.entry_date)}<span>入境</span></strong><button className="icon-button" aria-label={`编辑${dateLabel(stay.entry_date)}停留记录`} onClick={() => openStay(stay)}><Pencil size={14} /></button></div><div className="people-stay-facts"><span>准许停留至<b>{stay.permitted_until ? dateLabel(stay.permitted_until) : '期限待补充'}</b></span><span>离境日期<b>{stay.exit_date ? dateLabel(stay.exit_date) : '未录入'}</b></span></div>{(stay.permit_type || stay.permit_number) && <p className="people-stay-permit">{stay.permit_type || '许可类型未填写'}{stay.permit_number && <span>{stay.permit_number}</span>}</p>}{stay.purpose && <p>停留事由：{stay.purpose}</p>}<p className="people-stay-source"><FileText size={11} />来源：{stay.source}</p>{stay.notes && <p className="people-stay-notes">{stay.notes}</p>}</article>)}</div> : <div className="people-detail-empty"><CalendarDays size={22} /><p>暂无停留记录</p><small>添加已知入境事实，未知期限可以留空。</small></div>}</section>
        <section className="people-detail-section"><h3><FileCheck2 size={16} />信息来源与备注</h3><div className="people-source-note"><span>信息来源</span><p>{detail.source}</p></div>{detail.notes && <p className="people-detail-notes">{detail.notes}</p>}<div className="people-detail-meta"><span>创建于 {timeLabel(detail.created_at)}</span><span>更新于 {timeLabel(detail.updated_at)}</span></div></section>
      </>}
    </Dialog>}

    {editor && <Dialog title={editor.id ? '编辑人员档案' : '新建人员档案'} onClose={closeEditor} className="people-form-dialog"><p className="people-dialog-description">记录可核验的信息，带 <b>*</b> 的字段为必填项。档案编号将由系统生成。</p><form onSubmit={savePerson}><fieldset disabled={saving} className="people-form-fieldset"><section className="people-form-section"><h3><span>01</span>身份信息</h3><div className="people-form-grid"><Field label="姓名" value={form.name} onChange={value => changePerson('name', value)} required maxLength={100} placeholder="按证件填写姓名" /><Field label="英文姓名" value={form.english_name} onChange={value => changePerson('english_name', value)} maxLength={200} /><Field label="国籍" value={form.nationality} onChange={value => changePerson('nationality', value)} required maxLength={100} placeholder="如：中国、新加坡" /><label className="field people-field"><span>性别</span><select className="select" value={form.gender} onChange={event => changePerson('gender', event.target.value as PersonInput['gender'])}><option value="unknown">未知 / 未填写</option><option value="male">男</option><option value="female">女</option></select></label><Field label="出生日期" type="date" value={form.birth_date} onChange={value => changePerson('birth_date', value || null)} /><Field label="联系电话" value={form.phone} onChange={value => changePerson('phone', value)} maxLength={80} /></div></section><section className="people-form-section"><h3><span>02</span>证件与来华信息</h3><div className="people-form-grid"><label className="field people-field"><span>证件类型</span><select className="select" value={form.document_type} onChange={event => changePerson('document_type', event.target.value as PersonInput['document_type'])}><option value="passport">护照</option><option value="other">其他证件</option></select></label><Field label="证件号码" value={form.document_number} onChange={value => changePerson('document_number', value)} required maxLength={100} /><Field label="证件有效期至" type="date" value={form.document_expiry} onChange={value => changePerson('document_expiry', value || null)} /><Field label="来华目的" value={form.purpose} onChange={value => changePerson('purpose', value)} maxLength={500} placeholder="如：学习、探亲、商务" /><Field label="联系地址" value={form.address} onChange={value => changePerson('address', value)} full maxLength={1000} /></div></section><section className="people-form-section"><h3><span>03</span>来源与核实</h3><div className="people-form-grid"><Field label="信息来源" value={form.source} onChange={value => changePerson('source', value)} required maxLength={500} placeholder="如：本人申报、证件核验、业务材料" /><label className="field people-field"><span>核实状态</span><select className="select" value={form.verification} onChange={event => changePerson('verification', event.target.value as PersonInput['verification'])}><option value="pending">待核实</option><option value="verified">已核实</option></select></label><label className="field people-field people-field-full"><span>备注</span><textarea className="input" rows={3} value={form.notes} maxLength={5000} placeholder="补充事实、材料说明或待核实事项" onChange={event => changePerson('notes', event.target.value)} /></label></div><div className="people-form-hint"><ShieldCheck size={15} />“已核实”表示工作人员已核验信息，不由 AI 自动认定。</div></section></fieldset>{formError && <ErrorNotice message={formError} />}<div className="people-dialog-footer"><span>{formDirty ? '有未保存的修改' : '信息保存在本地数据库'}</span><button type="button" className="btn btn-secondary" disabled={saving} onClick={closeEditor}>取消</button><button type="submit" className="btn btn-primary" disabled={saving}>{saving ? <LoaderCircle size={15} className="people-spin" /> : <Check size={15} />}{saving ? '正在保存…' : '保存档案'}</button></div></form></Dialog>}

    {stayEditor && <Dialog title={stayEditor.id ? '编辑停留记录' : '添加停留记录'} onClose={closeStay} className="people-form-dialog people-stay-dialog"><p className="people-dialog-description">补充 {detail?.name || '该人员'} 的入境、停留许可与离境事实。</p><form onSubmit={saveStay}><fieldset disabled={staySaving} className="people-form-fieldset"><div className="people-form-grid"><Field label="入境日期" value={stayForm.entry_date} type="date" required onChange={value => changeStay('entry_date', value)} /><Field label="准许停留至" value={stayForm.permitted_until} type="date" onChange={value => changeStay('permitted_until', value || null)} /><Field label="离境日期" value={stayForm.exit_date} type="date" onChange={value => changeStay('exit_date', value || null)} /><Field label="许可类型" value={stayForm.permit_type} onChange={value => changeStay('permit_type', value)} maxLength={100} placeholder="按实际许可材料填写" /><Field label="许可号码" value={stayForm.permit_number} onChange={value => changeStay('permit_number', value)} maxLength={100} /><Field label="停留事由" value={stayForm.purpose} onChange={value => changeStay('purpose', value)} maxLength={500} /><Field label="信息来源" value={stayForm.source} onChange={value => changeStay('source', value)} required full maxLength={500} placeholder="填写依据的材料或信息来源" /><label className="field people-field people-field-full"><span>备注</span><textarea className="input" rows={3} value={stayForm.notes} maxLength={5000} onChange={event => changeStay('notes', event.target.value)} /></label></div><div className="people-form-hint"><CircleAlert size={16} />期限未知时请留空。未填写离境日期仅代表尚未录入，不等同于仍在境内。</div></fieldset>{stayError && <ErrorNotice message={stayError} />}<div className="people-dialog-footer"><span>{stayDirty ? '有未保存的修改' : '记录事实，不推断违法'}</span><button type="button" className="btn btn-secondary" onClick={closeStay} disabled={staySaving}>取消</button><button className="btn btn-primary" type="submit" disabled={staySaving}>{staySaving ? <LoaderCircle size={15} className="people-spin" /> : <Check size={15} />}{staySaving ? '正在保存…' : '保存记录'}</button></div></form></Dialog>}

    {archiveTarget && <Dialog title={archiveTarget.archived ? '恢复人员档案' : '归档人员档案'} onClose={() => { if (!archiving) setArchiveTarget(null); }} className="people-archive-dialog"><div className="people-archive-illustration"><Archive size={29} /></div><h3>{archiveTarget.archived ? '将此档案恢复到在用列表？' : '将此档案移至归档列表？'}</h3><p><strong>{archiveTarget.name}</strong><span>{archiveTarget.code}</span></p><div className="people-archive-explanation">{archiveTarget.archived ? '档案恢复后，可继续管理人员信息与停留记录。' : '归档会保留人员信息和全部停留记录，不会删除数据。之后可从“已归档”列表恢复。'}</div>{archiveError && <ErrorNotice message={archiveError} />}<div className="people-dialog-footer"><button className="btn btn-secondary" disabled={archiving} onClick={() => setArchiveTarget(null)}>取消</button><button className="btn btn-primary" disabled={archiving} onClick={changeArchive}>{archiving ? <LoaderCircle size={15} className="people-spin" /> : <Archive size={15} />}{archiving ? '正在处理…' : archiveTarget.archived ? '确认恢复' : '确认归档'}</button></div></Dialog>}

    {importOpen && <Dialog title="批量导入人员档案" onClose={closeImport} className="people-import-dialog"><div className="people-import-steps"><span className={!preview ? 'active' : 'done'}><i>{preview ? <Check size={12} /> : '1'}</i>选择文件</span><ChevronRight size={13} /><span className={preview ? 'active' : ''}><i>2</i>检查预览</span><ChevronRight size={13} /><span><i>3</i>确认导入</span></div><input ref={fileInput} className="people-file-input" type="file" accept=".csv,text/csv" aria-label="选择 CSV 文件" disabled={importing || previewLoading} onChange={event => { void readCsv(event.target.files?.[0]); event.target.value = ''; }} /><div className={`people-upload-zone ${fileName ? 'has-file' : ''}`}><div className="people-upload-symbol"><FileSpreadsheet size={29} /></div><div><h3>{fileName || '从标准 CSV 文件导入'}</h3><p>UTF-8 编码 · 单个文件不超过 1 MiB · 最多 500 行</p></div><button type="button" className="btn btn-secondary" disabled={previewLoading || importing} onClick={() => fileInput.current?.click()}>{previewLoading ? <LoaderCircle size={15} className="people-spin" /> : <ArrowUpFromLine size={15} />}{previewLoading ? '正在预览…' : fileName ? '重新选择' : '选择文件'}</button></div><div className="people-import-help"><span>还没有文件？</span><a href="/api/people/template.csv" download><ArrowDownToLine size={13} />下载 CSV 模板</a><span>证件重复或必填项缺失时，整批不导入。</span></div>{importError && <ErrorNotice message={importError} />}{preview && <><div className="people-preview-summary"><div><strong>{preview.total}</strong><span>文件记录</span></div><div className="success"><strong>{preview.valid_count}</strong><span>通过校验</span></div><div className={preview.errors.length ? 'danger' : ''}><strong>{preview.errors.length}</strong><span>校验问题</span></div></div>{preview.errors.length > 0 && <div className="people-import-errors" role="alert"><h4><CircleAlert size={16} />请修改以下问题后重新选择文件</h4><ul>{preview.errors.map((error, index) => <li key={`${error.row}-${index}`}><span>第 {error.row} 行</span>{error.message}</li>)}</ul></div>}{preview.rows.length > 0 && <div className="people-preview-table"><div className="people-preview-title">导入预览 <span>展示前 {Math.min(preview.rows.length, 20)} 条有效记录</span></div><div className="people-preview-scroll"><table><thead><tr><th>姓名</th><th>国籍</th><th>证件号码</th><th>核实状态</th><th>来源</th></tr></thead><tbody>{preview.rows.slice(0, 20).map((row, index) => <tr key={index}><td>{row.name}</td><td>{row.nationality}</td><td>{row.document_number}</td><td><Verification value={row.verification} /></td><td>{row.source}</td></tr>)}</tbody></table></div></div>}<div className="people-import-promise"><ShieldCheck size={15} /><span>{preview.errors.length ? '当前存在错误，不能提交。请修正整批文件后重新预览。' : `确认后将一次性保存 ${preview.valid_count} 条档案，提交时仍会再次校验重复与字段。`}</span></div></>}<div className="people-dialog-footer"><span>{preview ? '先预览，再写入本地数据库' : '文件尚未写入数据库'}</span><button className="btn btn-secondary" disabled={importing} onClick={closeImport}>取消</button><button className="btn btn-primary" disabled={!preview || preview.errors.length > 0 || !preview.valid_count || previewLoading || importing} onClick={confirmImport}>{importing ? <LoaderCircle size={15} className="people-spin" /> : <Check size={15} />}{importing ? '正在导入…' : `确认导入${preview?.valid_count ? ` ${preview.valid_count} 条` : ''}`}</button></div></Dialog>}
  </div>;
}
