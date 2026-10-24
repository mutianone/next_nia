import { useListPosition, useViewState } from '../hooks/useWorkspaceView';
import { formatDateTime } from '../lib/display';
import { PageHeading } from '../components/PageHeading';
import { Choice } from '../components/Choice';
import { usePageDirty } from '../hooks/usePageDirty';
import { confirmAction } from '../lib/confirmation';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { ArrowRight, Bell, CalendarClock, CalendarDays, Check, CheckCircle2, ChevronLeft, ChevronRight, CircleAlert, ClipboardCheck, Clock3, FileCheck2, FileText, History, LoaderCircle, MessageSquareText, Plus, Radar, RefreshCw, RotateCcw, Search, Settings2, ShieldCheck, SlidersHorizontal, UserRound, X } from 'lucide-react';
import { api } from '../api';
import { Dialog } from '../components/Dialog';
import type { PageId } from '../navigation';
import type { PeopleResponse, Person } from '../people-types';
import type { AlertAction, AlertCategory, AlertDetail, AlertItem, AlertList, AlertRuleInput, AlertRules, AlertStatus, ScanResult, StayFacts } from '../alert-types';
import { actionLabels, categoryLabels, statusLabels } from '../alert-types';

const dateText = (value?: string | null) => value ? value.slice(0, 10).replaceAll('-', '.') : '尚未登记';
const timeText = formatDateTime;
const errorText = (error: unknown) => error instanceof Error ? /fetch|network/i.test(error.message) ? '无法连接本地服务，请检查服务状态后重试。' : error.message : '操作未完成，请稍后重试。';
const emptyFacts = (): StayFacts => ({ visa_valid_until: null, extension_status: 'none', extension_until: null, receipt_valid_until: null, verified: false, source: '', notes: '' });
const factFields = (value: StayFacts): StayFacts => ({ visa_valid_until: value.visa_valid_until || null, extension_status: value.extension_status, extension_until: value.extension_until || null, receipt_valid_until: value.receipt_valid_until || null, verified: value.verified, source: value.source || '', notes: value.notes || '' });
const ruleFields = (value: AlertRules): AlertRuleInput => ({ enabled: value.enabled, upcoming_days: value.upcoming_days, urgent_days: value.urgent_days, scan_interval_minutes: value.scan_interval_minutes });
const daysText = (item: AlertItem) => item.days_remaining === null ? '待核查' : item.days_remaining < 0 ? `超过 ${Math.abs(item.days_remaining)} 天` : item.days_remaining === 0 ? '今天届满' : `剩余 ${item.days_remaining} 天`;

function ErrorBox({ children, retry }: { children: ReactNode; retry?: () => void }) { return <div className="al-error" role="alert"><CircleAlert size={17}/><span>{children}</span>{retry && <button type="button" onClick={retry}>重试</button>}</div>; }
function StateBadge({ status }: { status: AlertStatus }) { return <span className={`al-state ${status}`}><i/>{statusLabels[status]}</span>; }
function CategoryBadge({ category }: { category: AlertCategory }) { return <span className={`al-category ${category}`}>{categoryLabels[category]}</span>; }
function Loading({ children = '正在读取本地记录…' }: { children?: ReactNode }) { return <div className="al-empty" role="status"><LoaderCircle className="spinning" size={25}/><p>{children}</p></div>; }
function useReportDirty(dirty: boolean, report: (dirty: boolean) => void) { useEffect(() => { report(dirty); return () => report(false); }, [dirty, report]); }
function ConfirmedField({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) { return <label className={`field al-field ${wide ? 'al-wide' : ''}`}><span>{label}</span>{children}</label>; }

function RulesDialog({ current, onClose, onSaved, onDirty }: { current: AlertRules; onClose: () => void; onSaved: (value: AlertRules) => void; onDirty: (dirty: boolean) => void }) {
  const [form, setForm] = useState<AlertRuleInput>(() => ruleFields(current));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const dirty = JSON.stringify(form) !== JSON.stringify(ruleFields(current));
  useReportDirty(dirty || saving, onDirty);
  const close = async () => { if (saving || (dirty && !(await confirmAction('预警规则尚未保存，确定放弃修改吗？', {"title":"保留这次修改？","confirmLabel":"放弃修改","cancelLabel":"继续编辑","tone":"warning"})))) return; onClose(); };
  async function save(event: FormEvent) {
    event.preventDefault(); if (saving) return;
    if (!Number.isInteger(form.upcoming_days) || !Number.isInteger(form.urgent_days) || !Number.isInteger(form.scan_interval_minutes) || form.urgent_days > form.upcoming_days) { setError('请填写整数，重点关注天数不能大于提前提醒天数。'); return; }
    setSaving(true); setError('');
    try { const result = await api<AlertRules>('/alerts/rules', { method: 'PUT', body: JSON.stringify(form) }); if (typeof result?.enabled !== 'boolean') throw new Error('规则保存响应不完整，请重新读取确认。'); onSaved(result); } catch (error) { setError(errorText(error)); } finally { setSaving(false); }
  }
  return <Dialog title="预警扫描规则" className="al-rules-dialog" onClose={close}><form onSubmit={save}>
    <div className="al-dialog-lead"><span><SlidersHorizontal size={24}/></span><div><h3>把提醒提前，把事实核清。</h3><p>规则在本地运行，仅对已登记的停留事实生成提醒。</p></div></div>
    <label className="al-switch-row"><div><strong>启用自动扫描</strong><small>由服务端按设定周期运行；关闭后仍可手动扫描。</small></div><input type="checkbox" role="switch" checked={form.enabled} onChange={event => setForm({ ...form, enabled: event.target.checked })} disabled={saving}/><span className="al-switch-track" aria-hidden="true"/></label>
    <fieldset className="al-fieldset" disabled={saving}><div className="al-form-grid">
      <ConfirmedField label="提前提醒天数"><input className="input" type="number" min={0} max={365} step={1} required value={form.upcoming_days} onChange={event => setForm({ ...form, upcoming_days: Number(event.target.value) })}/><small>进入此时间窗口后提醒；设为 0 则不提前提醒。</small></ConfirmedField>
      <ConfirmedField label="重点关注天数"><input className="input" type="number" min={0} max={form.upcoming_days} step={1} required value={form.urgent_days} onChange={event => setForm({ ...form, urgent_days: Number(event.target.value) })}/><small>用于临近期限定级，不能超过提前提醒天数。</small></ConfirmedField>
      <ConfirmedField label="扫描间隔（分钟）" wide><input className="input" type="number" min={1} max={10080} step={1} required value={form.scan_interval_minutes} onChange={event => setForm({ ...form, scan_interval_minutes: Number(event.target.value) })}/><small>服务运行时生效。更改规则不会立即产生新的扫描结果。</small></ConfirmedField>
    </div></fieldset>
    <div className="al-rules-footnote"><Clock3 size={15}/><span>上次扫描 {timeText(current.last_scan_at)}<br/>下次扫描 {current.enabled ? timeText(current.next_scan_at) : '自动扫描已关闭'}</span></div>
    {error && <ErrorBox>{error}</ErrorBox>}<div className="al-form-footer"><span>{dirty ? '有尚未保存的修改' : '规则保存在当前环境'}</span><button type="button" className="btn btn-secondary" disabled={saving} onClick={close}>取消</button><button className="btn btn-primary" disabled={saving || !dirty}>{saving ? <LoaderCircle size={16} className="spinning"/> : <Check size={16}/>}保存规则</button></div>
  </form></Dialog>;
}

function FactsDialog({ initialPersonId, initialStayId, onClose, onSaved, onDirty, onPeople }: { initialPersonId?: string; initialStayId?: string; onClose: () => void; onSaved: () => void; onDirty: (dirty: boolean) => void; onPeople: () => void }) {
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [people, setPeople] = useState<PeopleResponse | null>(null);
  const [peopleLoading, setPeopleLoading] = useState(!initialPersonId);
  const [peopleError, setPeopleError] = useState('');
  const [peopleReload, setPeopleReload] = useState(0);
  const [personId, setPersonId] = useState(initialPersonId || '');
  const [person, setPerson] = useState<Person | null>(null);
  const [personLoading, setPersonLoading] = useState(Boolean(initialPersonId));
  const [personError, setPersonError] = useState('');
  const [personReload, setPersonReload] = useState(0);
  const [stayId, setStayId] = useState(initialStayId || '');
  const [facts, setFacts] = useState<StayFacts | null>(null);
  const [form, setForm] = useState<StayFacts>(emptyFacts);
  const [factsLoading, setFactsLoading] = useState(Boolean(initialStayId));
  const [factsError, setFactsError] = useState('');
  const [factsReload, setFactsReload] = useState(0);
  const [saving, setSaving] = useState(false);
  const dirty = Boolean(facts && JSON.stringify(form) !== JSON.stringify(facts));
  useReportDirty(dirty || saving, onDirty);
  useEffect(() => { const timer = window.setTimeout(() => setSearch(query.trim()), 300); return () => window.clearTimeout(timer); }, [query]);
  useEffect(() => {
    if (initialPersonId) return;
    let cancelled = false; setPeopleLoading(true); setPeopleError('');
    api<PeopleResponse>(`/people?${new URLSearchParams({ q: search, page: '1', page_size: '20', archived: 'false' })}`).then(value => { if (!value || !Array.isArray(value.items)) throw new Error('人员列表响应不完整。'); if (!cancelled) setPeople(value); }).catch(error => { if (!cancelled) { setPeopleError(errorText(error)); setPeople(null); } }).finally(() => { if (!cancelled) setPeopleLoading(false); });
    return () => { cancelled = true; };
  }, [search, peopleReload, initialPersonId]);
  useEffect(() => {
    if (!personId) { setPerson(null); return; }
    let cancelled = false; setPersonLoading(true); setPersonError(''); setPerson(null);
    api<Person>(`/people/${encodeURIComponent(personId)}`).then(value => { if (!value?.id || !Array.isArray(value.stays)) throw new Error('人员停留记录响应不完整。'); if (!cancelled) setPerson(value); }).catch(error => { if (!cancelled) setPersonError(errorText(error)); }).finally(() => { if (!cancelled) setPersonLoading(false); });
    return () => { cancelled = true; };
  }, [personId, personReload]);
  useEffect(() => {
    setFacts(null); setForm(emptyFacts()); setFactsError('');
    if (!stayId) { setFactsLoading(false); return; }
    let cancelled = false; setFactsLoading(true);
    api<StayFacts>(`/alerts/stays/${encodeURIComponent(stayId)}/facts`).then(value => { if (!value || typeof value.verified !== 'boolean' || !['none', 'pending', 'approved', 'rejected'].includes(value.extension_status)) throw new Error('补充事实响应不完整，请重试。'); if (!cancelled) { const fields = factFields(value); setFacts(fields); setForm(fields); } }).catch(error => { if (!cancelled) setFactsError(errorText(error)); }).finally(() => { if (!cancelled) setFactsLoading(false); });
    return () => { cancelled = true; };
  }, [stayId, factsReload]);
  const canDiscard = async () => !saving && (!dirty || (await confirmAction('补充事实尚未保存，确定放弃修改吗？', {"title":"保留这次修改？","confirmLabel":"放弃修改","cancelLabel":"继续编辑","tone":"warning"})));
  const close = async () => { if ((await canDiscard())) onClose(); };
  const selectPerson = async (id: string) => { if (id === personId || !(await canDiscard())) return; setPersonId(id); setStayId(''); };
  const selectStay = async (id: string) => { if (id === stayId || !(await canDiscard())) return; setStayId(id); };
  const stay = person?.stays?.find(item => item.id === stayId);
  const change = <K extends keyof StayFacts>(key: K, value: StayFacts[K]) => { setForm(current => ({ ...current, [key]: value })); setFactsError(''); };
  async function save(event: FormEvent) {
    event.preventDefault(); if (!stayId || !facts || saving) return;
    if (form.verified && !form.source.trim()) { setFactsError('标记已核实时，请填写本次核查的信息来源。'); return; }
    if (form.verified && form.extension_status === 'approved' && !form.extension_until) { setFactsError('标记已核实且延期已获批准时，请登记批准后的停留截止日期。'); return; }
    setSaving(true); setFactsError('');
    try { const result = await api<StayFacts>(`/alerts/stays/${encodeURIComponent(stayId)}/facts`, { method: 'PUT', body: JSON.stringify(form) }); if (typeof result?.verified !== 'boolean') throw new Error('保存响应不完整，请重新打开核实保存状态。'); onSaved(); } catch (error) { setFactsError(errorText(error)); } finally { setSaving(false); }
  }
  return <Dialog title="补充停留核查事实" className="al-facts-dialog" onClose={close}>
    <div className="al-dialog-lead"><span><FileCheck2 size={24}/></span><div><h3>先登记证据，再更新提醒。</h3><p>尚无预警的人员，也可以在这里补充事实。</p></div></div>
    {!initialPersonId && <section className="al-person-picker"><label className="al-search"><Search size={16}/><input maxLength={200} value={query} disabled={saving} onChange={event => setQuery(event.target.value)} placeholder="搜索姓名、证件号码或国籍" aria-label="查找需要补充事实的人员"/></label>
      {peopleError ? <ErrorBox retry={() => setPeopleReload(value => value + 1)}>{peopleError}</ErrorBox> : peopleLoading ? <Loading>正在查找人员档案…</Loading> : <><div className="al-person-options">{people?.items.map(item => <button type="button" aria-pressed={personId === item.id} key={item.id} disabled={saving} onClick={() => selectPerson(item.id)}><span className="al-avatar">{[...item.name][0]}</span><span><strong>{item.name}</strong><small>{item.code} · {item.nationality}</small></span>{personId === item.id ? <CheckCircle2 size={17}/> : <ChevronRight size={15}/>}</button>)}</div>{!people?.items.length && <div className="al-picker-empty"><UserRound size={23}/><p>{search ? '没有匹配的在用人员档案，请调整关键词。' : '尚无在用人员档案，请先登记人员及停留记录。'}</p>{!search && <button type="button" className="text-button" onClick={onPeople}>前往人员档案<ArrowRight size={13}/></button>}</div>}{!!people && people.total > 20 && <p className="al-picker-caption">当前显示前 20 条，请通过姓名或证件号码缩小范围。</p>}</>}
    </section>}
    {personError && <ErrorBox retry={() => setPersonReload(value => value + 1)}>{personError}</ErrorBox>}
    {personLoading ? <Loading>正在读取停留记录…</Loading> : person && <><div className="al-selected-person"><UserRound size={18}/><strong>{person.name}</strong><span>{person.code}</span>{person.archived && <small>已归档</small>}</div><ConfirmedField label="选择停留记录"><Choice className="select" value={stayId} disabled={saving} onChange={event => selectStay(event.target.value)}><option value="">请选择一条停留记录</option>{person.stays?.map(item => <option key={item.id} value={item.id}>入境 {dateText(item.entry_date)} · {item.permit_type || '证件类型未填'} · {item.exit_date ? `已离境 ${dateText(item.exit_date)}` : '未登记离境'}</option>)}</Choice></ConfirmedField>{!person.stays?.length && <div className="al-picker-empty"><CalendarDays size={24}/><p>该人员尚无停留记录，请先在人员档案中登记。</p><button type="button" className="text-button" onClick={onPeople}>前往人员档案<ArrowRight size={13}/></button></div>}</>}
    {factsLoading && <Loading>正在读取已登记的核查事实…</Loading>}
    {stay && <div className="al-existing-facts"><span><small>入境日期</small><strong>{dateText(stay.entry_date)}</strong></span><span><small>已登记停留期限</small><strong>{dateText(stay.permitted_until)}</strong></span><span><small>离境日期</small><strong>{dateText(stay.exit_date)}</strong></span></div>}
    {facts && <form onSubmit={save}><div className="al-boundary"><ShieldCheck size={17}/><p>签证有效期不等于停留期限。延期申请待审批、受理回执有效期均不自动确定合法停留期限；日期未知时请留空。</p></div><fieldset className="al-fieldset" disabled={saving}><div className="al-form-grid">
      <ConfirmedField label="签证有效截止日期"><input className="input" type="date" value={form.visa_valid_until || ''} onChange={event => change('visa_valid_until', event.target.value || null)}/><small>仅记录签证事实，不替代停留期限。</small></ConfirmedField>
      <ConfirmedField label="延期申请状态"><Choice className="select" value={form.extension_status} onChange={event => change('extension_status', event.target.value as StayFacts['extension_status'])}><option value="none">未登记延期申请</option><option value="pending">已申请 · 待审批</option><option value="approved">已获批准</option><option value="rejected">未获批准</option></Choice></ConfirmedField>
      <ConfirmedField label="批准延期后的停留截止日期"><input className="input" type="date" value={form.extension_until || ''} required={form.verified && form.extension_status === 'approved'} onChange={event => change('extension_until', event.target.value || null)}/><small>以批准文件为准，请勿填写预期批准日期。</small></ConfirmedField>
      <ConfirmedField label="受理回执有效截止日期"><input className="input" type="date" value={form.receipt_valid_until || ''} onChange={event => change('receipt_valid_until', event.target.value || null)}/><small>回执需人工核查，不直接作为停留许可。</small></ConfirmedField>
      <ConfirmedField label={form.verified ? '信息来源 *' : '信息来源'} wide><input className="input" required={form.verified} maxLength={500} value={form.source} onChange={event => change('source', event.target.value)} placeholder="例如：当事人提交的批准文件，经原件核验"/><small>标记已核实时必须填写；未确认材料可保留待核实。</small></ConfirmedField>
      <ConfirmedField label="核查说明" wide><textarea className="input al-textarea" rows={3} maxLength={5000} value={form.notes} onChange={event => change('notes', event.target.value)} placeholder="登记材料、核查过程与仍待确认的事项"/></ConfirmedField>
    </div><label className="al-verified"><input type="checkbox" checked={form.verified} onChange={event => change('verified', event.target.checked)}/><span><strong>已人工核实本次补充事实</strong><small>仅表示材料核实状态，不构成违法认定或处置决定。</small></span></label></fieldset>
      {factsError && <ErrorBox>{factsError}</ErrorBox>}<div className="al-form-footer"><span>{dirty ? '有尚未保存的事实' : '保存后请扫描以更新提醒'}</span><button type="button" className="btn btn-secondary" disabled={saving} onClick={close}>取消</button><button className="btn btn-primary" disabled={saving || !dirty}>{saving ? <LoaderCircle className="spinning" size={16}/> : <Check size={16}/>}保存事实</button></div>
    </form>}
    {!facts && factsError && <ErrorBox retry={() => setFactsReload(value => value + 1)}>{factsError}</ErrorBox>}
  </Dialog>;
}

function DetailDialog({ id, onClose, onChanged, onFacts, onDocuments, onDirty }: { id: string; onClose: () => void; onChanged: () => void; onFacts: (item: AlertItem) => void; onDocuments: (item: AlertItem) => void; onDirty: (dirty: boolean) => void }) {
  const [item, setItem] = useState<AlertDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [action, setAction] = useState<AlertAction>('note');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');
  useReportDirty(Boolean(note) || saving, onDirty);
  useEffect(() => {
    let cancelled = false; setLoading(true); setError('');
    api<AlertDetail>(`/alerts/${encodeURIComponent(id)}`).then(value => { if (!value?.id || !Array.isArray(value.history)) throw new Error('预警详情响应不完整。'); if (!cancelled) setItem(value); }).catch(error => { if (!cancelled) setError(errorText(error)); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [id, reload]);
  const canLeave = async () => !saving && (!note || (await confirmAction('处置备注尚未提交，确定放弃本次填写吗？', {"title":"保留这次修改？","confirmLabel":"放弃修改","cancelLabel":"继续编辑","tone":"warning"})));
  const close = async () => { if ((await canLeave())) onClose(); };
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!item || saving || !note.trim()) return;
    setSaving(true); setError(''); setNotice('');
    try { const result = await api<AlertDetail>(`/alerts/${encodeURIComponent(id)}/actions`, { method: 'POST', body: JSON.stringify({ action, note: note.trim() }) }); if (!result?.id || !Array.isArray(result.history)) throw new Error('操作响应不完整，请刷新确认处置状态。'); setItem(result); setNote(''); setAction('note'); setNotice('处置记录已保存在本地。'); onChanged(); } catch (error) { setError(errorText(error)); } finally { setSaving(false); }
  }
  return <Dialog title="期限提醒详情" className="al-detail-dialog" onClose={close}>
    {loading && <Loading/>}{error && <ErrorBox retry={!saving ? () => setReload(value => value + 1) : undefined}>{error}</ErrorBox>}
    {item && !loading && <><div className="al-detail-person"><span className="al-avatar large">{[...item.person_name][0] || '人'}</span><div><span className="al-detail-code">{item.person_code}</span><h3>{item.person_name}</h3></div><StateBadge status={item.status}/></div>
      <div className={`al-deadline-card ${item.category}`}><div><CategoryBadge category={item.category}/><strong>{daysText(item)}</strong><small>基于当前已登记事实 · 待人工核查</small></div><div><CalendarClock size={27}/><span>登记截止日期</span><b>{dateText(item.deadline)}</b></div></div>
      <section className="al-detail-section"><h4><ClipboardCheck size={16}/>触发依据</h4><p className="al-reason">{item.reason}</p><dl className="al-detail-meta"><div><dt>信息来源</dt><dd>{item.source_summary || '来源尚待补充'}</dd></div><div><dt>关注级别</dt><dd>{item.priority === 'urgent' ? '重点关注' : item.priority === 'review' ? '事实待核查' : '常规提醒'}</dd></div><div><dt>签证有效截止（独立记录）</dt><dd>{dateText(item.visa_valid_until)} · 不等于停留期限</dd></div><div><dt>首次发现</dt><dd>{timeText(item.created_at)}</dd></div><div><dt>最近扫描识别</dt><dd>{timeText(item.last_seen_at)}</dd></div><div><dt>更新于</dt><dd>{timeText(item.updated_at)}</dd></div>{item.auto_closed && <div><dt>关闭方式</dt><dd>扫描自动关闭</dd></div>}</dl><div className="al-boundary"><ShieldCheck size={16}/><p>这是期限待核查提醒，不代表违法认定。请核对停留许可、延期批准及实际离境等事实后处置。</p></div><div className="al-detail-links"><button type="button" className="btn btn-secondary" disabled={saving} onClick={async () => { if ((await canLeave())) onFacts(item); }}><FileCheck2 size={15}/>补充核查事实</button><button type="button" className="btn btn-secondary" disabled={saving} onClick={() => onDocuments(item)}><FileText size={15}/>关联起草文书<ArrowRight size={14}/></button></div></section>
      <section className="al-detail-section"><h4><MessageSquareText size={16}/>记录核查进展</h4><form onSubmit={submit}><div className="al-action-choices" role="group" aria-label="选择处置动作">{([...(item.status === 'open' ? ['start'] : []), ...(item.status !== 'closed' ? ['close'] : ['reopen']), 'note'] as AlertAction[]).map(value => <button type="button" aria-pressed={action === value} key={value} disabled={saving} onClick={() => setAction(value)}>{value === 'reopen' ? <RotateCcw size={13}/> : value === 'close' ? <CheckCircle2 size={13}/> : value === 'start' ? <Search size={13}/> : <Plus size={13}/>} {actionLabels[value]}</button>)}</div><label className="field al-field"><span>处置备注 <b>*</b></span><textarea className="input al-textarea" rows={3} required maxLength={2000} value={note} disabled={saving} onChange={event => setNote(event.target.value)} placeholder={action === 'close' ? '说明关闭理由、核查依据及处理结果' : action === 'reopen' ? '说明重新打开的理由与待核查事项' : '记录核查材料、进展或后续安排'}/></label>{notice && <div className="al-success" role="status"><CheckCircle2 size={16}/>{notice}</div>}<div className="al-form-footer"><span>{note.length}/2000 · 处置记录将留存</span><button className="btn btn-primary" disabled={saving || !note.trim()}>{saving ? <LoaderCircle className="spinning" size={15}/> : <Check size={15}/>}提交{actionLabels[action]}</button></div></form></section>
      <section className="al-detail-section"><h4><History size={16}/>处置时间线 <span>{item.history.length}</span></h4>{item.history.length ? <ol className="al-history">{[...item.history].sort((a, b) => b.created_at.localeCompare(a.created_at)).map(record => <li key={record.id}><span className="al-history-dot"/><div><strong>{actionLabels[record.action] || record.action}</strong><time>{timeText(record.created_at)}</time><p>{record.note || '系统扫描记录'}</p></div></li>)}</ol> : <p className="al-muted-copy">暂无处置记录。提交备注后会在这里留存。</p>}</section>
    </>}
  </Dialog>;
}

export function Alerts({ onNavigate, onCreateDocument, initialId, onContextConsumed }: { initialId?: string | null; onContextConsumed?: () => void; onNavigate: (page: PageId) => void; onCreateDocument?: (context: { person_id: string; stay_id: string; alert_id: string }) => void }) {
  const [data, setData] = useState<AlertList | null>(null);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [query, setQuery] = useViewState('alerts.query', '');
  const [search, setSearch] = useViewState('alerts.search', '');
  const [status, setStatus] = useViewState<'active' | 'all' | AlertStatus>('alerts.status', 'active');
  const [category, setCategory] = useViewState<'all' | AlertCategory>('alerts.category', 'all');
  const [page, setPage] = useViewState('alerts.page', 1);
  const [reload, setReload] = useState(0);
  const [rules, setRules] = useState<AlertRules | null>(null);
  const [rulesError, setRulesError] = useState('');
  const [rulesLoading, setRulesLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState('');
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [notice, setNotice] = useState('');
  const [modal, setModal] = useState<'rules' | 'detail' | 'facts' | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [factsTarget, setFactsTarget] = useState<{ personId: string; stayId: string } | null>(null);
  const [childDirty, setChildDirty] = useState(false);
  const scanLock = useRef(false);
  const reportDirty = useCallback((dirty: boolean) => setChildDirty(dirty), []);
  const dirty = childDirty || scanning;
  usePageDirty(dirty);
  const listView = useListPosition(`alerts:${JSON.stringify([search,status,category,page])}`, !modal, !loading);
  useEffect(() => { if (initialId) { setSelectedId(initialId); setModal('detail'); onContextConsumed?.(); } }, [initialId, onContextConsumed]);
  useEffect(() => { if (query.trim() === search) return; const timer = window.setTimeout(() => { setSearch(query.trim()); setPage(1); }, 300); return () => window.clearTimeout(timer); }, [query, search, setSearch, setPage]);
  useEffect(() => {
    let cancelled = false; setLoading(true); setListError('');
    api<AlertList>(`/alerts?${new URLSearchParams({ q: search, status, category, page: String(page), page_size: '12' })}`).then(value => {
      if (!value || !Array.isArray(value.items) || !value.stats) throw new Error('预警列表响应不完整。');
      if (cancelled) return;
      const lastPage = Math.max(1, Math.ceil(value.total / 12));
      if (page > lastPage) { setPage(lastPage); return; }
      setData(value);
    }).catch(error => { if (!cancelled) { setListError(errorText(error)); setData(null); } }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [search, status, category, page, reload]);
  const loadRules = useCallback(async () => { setRulesLoading(true); setRulesError(''); try { const value = await api<AlertRules>('/alerts/rules'); if (typeof value?.enabled !== 'boolean') throw new Error('预警规则响应不完整。'); setRules(value); } catch (error) { setRulesError(errorText(error)); setRules(null); } finally { setRulesLoading(false); } }, []);
  useEffect(() => { void loadRules(); }, [loadRules]);
  const refresh = () => { setReload(value => value + 1); void loadRules(); };
  async function scan() {
    if (scanLock.current) return; scanLock.current = true; setScanning(true); setScanError(''); setScanResult(null); setNotice('');
    try { const value = await api<ScanResult>('/alerts/scan', { method: 'POST' }); if (!value?.scanned_at || ![value.created, value.updated, value.closed, value.scanned].every(count => typeof count === 'number')) throw new Error('扫描响应不完整，请刷新查看最新结果。'); setScanResult(value); refresh(); } catch (error) { setScanError(errorText(error)); } finally { scanLock.current = false; setScanning(false); }
  }
  const openFacts = (item?: AlertItem) => { setFactsTarget(item ? { personId: item.person_id, stayId: item.stay_id } : null); setModal('facts'); };
  const closeFacts = () => { if (factsTarget && selectedId) setModal('detail'); else setModal(null); };
  const totalPages = Math.max(1, Math.ceil((data?.total || 0) / 12));
  const stats = [
    { label: '当前待处理', value: data?.stats.active, icon: Bell, category: 'all', detail: '待处理与核查中', tone: 'primary' },
    { label: '临近登记期限', value: data?.stats.due_soon, icon: CalendarClock, category: 'due_soon', detail: '提前核查，及时跟进', tone: 'warning' },
    { label: '今日届满', value: data?.stats.due_today, icon: Clock3, category: 'due_today', detail: '登记期限为今日', tone: 'warning' },
    { label: '超过登记期限', value: data?.stats.past_due, icon: CircleAlert, category: 'past_due', detail: '核对延期与离境事实', tone: 'danger' },
    { label: '事实待核查', value: data?.stats.needs_review, icon: ClipboardCheck, category: 'needs_review', detail: '期限或材料仍待确认', tone: 'primary' },
  ] as const;
  return <div className="page-content alerts-page">
    <PageHeading title="期限核查" description="查看待核查事项，补充事实并记录处理结果。" scene="alerts"><div className="al-heading-actions"><button className="btn btn-secondary" disabled={scanning} onClick={() => openFacts()}><Plus size={16}/>补充核查事实</button><button className="btn btn-primary" disabled={scanning} onClick={scan}>{scanning ? <LoaderCircle size={16} className="spinning"/> : <Radar size={16}/>} {scanning ? '扫描中…' : '立即扫描'}</button></div></PageHeading>
    <div className="al-scan-strip"><span><i className={rules?.enabled ? 'active' : ''}/>{rulesLoading ? '读取扫描规则…' : rules ? rules.enabled ? `自动扫描 · 每 ${rules.scan_interval_minutes} 分钟` : '自动扫描已关闭' : '扫描规则读取失败'}</span><div><span>上次 <strong>{timeText(rules?.last_scan_at || data?.last_scan_at)}</strong></span><span>下次 <strong>{rules?.enabled ? timeText(rules.next_scan_at) : '—'}</strong></span><button type="button" disabled={!rules || rulesLoading || scanning} onClick={() => setModal('rules')}><Settings2 size={14}/>配置规则</button></div></div>
    {rulesError && <ErrorBox retry={loadRules}>{rulesError}</ErrorBox>}{rules?.last_error && <ErrorBox>最近一次自动扫描未完成：{rules.last_error}</ErrorBox>}
    {scanning && <div className="al-scan-progress" role="status"><span className="al-progress-line"/><LoaderCircle className="spinning" size={18}/><div><strong>正在扫描本地停留记录</strong><p>核对登记期限与补充事实，并更新提醒状态。请等待本次扫描完成。</p></div></div>}
    {scanError && <ErrorBox retry={scan}>{scanError}</ErrorBox>}{scanResult && <div className="al-scan-result" role="status"><CheckCircle2 size={19}/><div><strong>扫描完成</strong><p>检查 {scanResult.scanned} 条记录 · 新建 {scanResult.created} · 更新 {scanResult.updated} · 关闭 {scanResult.closed}</p></div><time>{timeText(scanResult.scanned_at)}</time><button className="icon-button" aria-label="关闭扫描结果提示" onClick={() => setScanResult(null)}><X size={15}/></button></div>}
    {notice && <div className="al-success" role="status"><CheckCircle2 size={17}/><span>{notice}</span><button className="icon-button" aria-label="关闭提示" onClick={() => setNotice('')}><X size={15}/></button></div>}
    <section className="al-stats" aria-label="待处理预警统计">{stats.map(stat => <button key={stat.category} className={`al-stat ${stat.tone} ${category === stat.category && status === 'active' ? 'selected' : ''}`} aria-pressed={category === stat.category && status === 'active'} onClick={() => { setCategory(stat.category); setStatus('active'); setPage(1); }}><div><span>{stat.label}</span><stat.icon size={17}/></div><strong>{stat.value === undefined ? '—' : stat.value.toLocaleString()}<small>条</small></strong><p>{stat.detail}<ChevronRight size={12}/></p></button>)}</section>
    <section className="panel al-register"><div className="al-register-heading"><div><span className="al-kicker">REVIEW QUEUE</span><h2>核查任务册 <span>{data?.total ?? '—'}</span></h2></div><button className="icon-button" disabled={loading || scanning} onClick={refresh} aria-label="刷新预警列表"><RefreshCw size={17} className={loading ? 'spinning' : ''}/></button></div>
      <div className="al-filter-bar"><div className="al-status-tabs" role="group" aria-label="按处置状态筛选">{([{ value: 'active', label: '当前待处理' }, { value: 'open', label: '待处理' }, { value: 'in_progress', label: '核查中' }, { value: 'closed', label: '已关闭' }, { value: 'all', label: '全部' }] as const).map(option => <button aria-pressed={status === option.value} key={option.value} onClick={() => { setStatus(option.value); setPage(1); }}>{option.label}</button>)}</div><div className="al-filter-inputs"><label className="al-search"><Search size={15}/><input value={query} maxLength={200} onChange={event => setQuery(event.target.value)} placeholder="搜索人员姓名、档案编号…" aria-label="搜索期限提醒"/>{query && <button type="button" onClick={() => setQuery('')} aria-label="清空搜索"><X size={14}/></button>}</label><label className="al-category-filter"><SlidersHorizontal size={14}/><Choice value={category} onChange={event => { setCategory(event.target.value as typeof category); setPage(1); }} aria-label="按提醒类别筛选"><option value="all">全部类别</option>{Object.entries(categoryLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</Choice></label></div></div>
      {listError ? <div className="al-list-error"><ErrorBox retry={refresh}>{listError}</ErrorBox></div> : loading && !data ? <Loading/> : data?.items.length ? <><div className="al-table-wrap" aria-busy={loading}><table className="al-table"><thead><tr><th>人员档案</th><th>提醒事项</th><th>登记截止日期</th><th>期限提示</th><th>处置状态</th><th>最近识别</th><th><span className="al-sr-only">查看详情</span></th></tr></thead><tbody>{data.items.map(item => <tr data-work-row={item.id} data-selected={listView.selectedRow===item.id} key={item.id}><td><button className="al-person-button" onClick={() => { listView.remember(item.id); setSelectedId(item.id); setModal('detail'); }}><span className="al-avatar">{[...item.person_name][0] || '人'}</span><span><strong>{item.person_name}</strong><small>{item.person_code}</small></span></button></td><td><CategoryBadge category={item.category}/><p className="al-reason-excerpt" title={item.reason}>{item.priority === 'urgent' && <span className="al-priority">重点关注 · </span>}{item.reason}</p></td><td className="al-date-cell">{dateText(item.deadline)}</td><td><span className={`al-days ${item.category}`}>{daysText(item)}</span></td><td><StateBadge status={item.status}/></td><td className="al-date-cell">{timeText(item.last_seen_at)}</td><td><button className="icon-button" aria-label={`查看${item.person_name}的期限提醒`} onClick={() => { listView.remember(item.id); setSelectedId(item.id); setModal('detail'); }}><ChevronRight size={17}/></button></td></tr>)}</tbody></table></div><div className="al-mobile-list" aria-busy={loading}>{data.items.map(item => <article className="al-mobile-card" key={item.id} data-work-row={item.id} data-selected={listView.selectedRow===item.id}><header><span className="al-avatar">{[...item.person_name][0] || '人'}</span><div><strong>{item.person_name}</strong><small>{item.person_code}</small></div><StateBadge status={item.status}/></header><div className="al-mobile-category"><CategoryBadge category={item.category}/><span className={`al-days ${item.category}`}>{daysText(item)}</span></div><p>{item.priority === 'urgent' && <span className="al-priority">重点关注 · </span>}{item.reason}</p><footer><span>登记期限 {dateText(item.deadline)}</span><button onClick={() => { listView.remember(item.id); setSelectedId(item.id); setModal('detail'); }}>核查详情<ArrowRight size={13}/></button></footer></article>)}</div>{loading && <div className="al-list-loading" role="status"><LoaderCircle className="spinning" size={13}/>正在更新列表…</div>}</> : <div className="al-empty large"><span className="al-empty-art"><ShieldCheck size={35}/></span><h3>{search || category !== 'all' || status !== 'active' ? '没有符合筛选条件的提醒' : data?.last_scan_at ? '当前没有待处理提醒' : '从一次事实扫描开始'}</h3><p>{search || category !== 'all' || status !== 'active' ? '调整筛选条件，查看其他提醒记录。' : '登记人员及停留事实后运行扫描。没有提醒并不等于系统已确认停留状态合法。'}</p>{search || category !== 'all' || status !== 'active' ? <button className="btn btn-secondary" onClick={() => { setQuery(''); setSearch(''); setCategory('all'); setStatus('active'); setPage(1); }}>重置筛选</button> : <div><button className="btn btn-secondary" onClick={() => openFacts()}><Plus size={15}/>补充事实</button><button className="btn btn-primary" disabled={scanning} onClick={scan}><Radar size={15}/>运行扫描</button></div>}</div>}
      <footer className="al-pagination"><span>{data ? `共 ${data.total} 条提醒` : '等待本地数据'}<small> · 以已登记事实为准</small></span><div><button className="icon-button" aria-label="上一页" disabled={page <= 1 || loading} onClick={() => setPage(value => value - 1)}><ChevronLeft size={17}/></button><span>{page} / {totalPages}</span><button className="icon-button" aria-label="下一页" disabled={page >= totalPages || loading} onClick={() => setPage(value => value + 1)}><ChevronRight size={17}/></button></div></footer>
    </section><div className="al-bottom-note"><ShieldCheck size={16}/><p>签证有效期、停留期限与延期审批分别记录。未知事实保留为待核查，系统不自动推断违法或作出处置决定。</p></div>
    {modal === 'rules' && rules && <RulesDialog current={rules} onClose={() => setModal(null)} onDirty={reportDirty} onSaved={value => { setRules(value); setModal(null); setNotice('预警规则已保存。可立即扫描以按新规则更新提醒。'); }}/>}
    {modal === 'detail' && <DetailDialog key={selectedId} id={selectedId} onClose={() => setModal(null)} onChanged={refresh} onFacts={openFacts} onDocuments={item => onCreateDocument ? onCreateDocument({ person_id: item.person_id, stay_id: item.stay_id, alert_id: item.id }) : onNavigate('documents')} onDirty={reportDirty}/>}
    {modal === 'facts' && <FactsDialog initialPersonId={factsTarget?.personId} initialStayId={factsTarget?.stayId} onClose={closeFacts} onDirty={reportDirty} onPeople={() => onNavigate('people')} onSaved={() => { closeFacts(); setNotice('补充事实已保存。请运行扫描以更新相关提醒。'); refresh(); }}/>}

  </div>;
}

export default Alerts;
