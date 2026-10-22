import { useMemo, useState } from 'react';
import { CalendarDays, FileSearch, RotateCcw } from 'lucide-react';
import { formatDateTime } from '../../lib/display';
import type { Profile } from '../../pages/Profiles';
export const eventLabels: Record<string, string> = { person: '档案登记', stay: '出入境', fact: '事实登记', record: '询问笔录', document: '业务文书', alert: '期限核查' };
const statusLabels: Record<string, string> = { draft: '草稿', reviewed: '已核对', archived: '已归档', open: '待核查', in_progress: '核查中', closed: '已关闭', pending: '待核验', verified: '已核验' };
export function eventDate(value: string) { return value.length === 10 ? value : formatDateTime(value); }
export function timelineMonths(timeline: Profile['timeline']) {
  const dates = timeline.map(t => eventDate(t.date).slice(0, 7)).filter(d => /^\d{4}-\d{2}$/.test(d)).sort();
  if (!dates.length) return [];
  const monthIndex = (date: string) => Number(date.slice(0, 4)) * 12 + Number(date.slice(5)) - 1;
  const start = monthIndex(dates[0]), end = monthIndex(dates[dates.length - 1]);
  return Array.from({ length: end - start + 1 }, (_, i) => `${String(Math.floor((start + i) / 12)).padStart(4, '0')}-${String((start + i) % 12 + 1).padStart(2, '0')}`);
}
export function ProfileTimeline({ timeline, onEvidence }: { timeline: Profile['timeline']; onEvidence: (event: Profile['timeline'][number]) => void }) {
  const months = useMemo(() => timelineMonths(timeline), [timeline]);
  const [kind, setKind] = useState('all'), [range, setRange] = useState<[number, number]>([0, Math.max(0, months.length - 1)]), [selected, setSelected] = useState('');
  const filtered = timeline.filter(t => (kind === 'all' || t.kind === kind) && eventDate(t.date).slice(0, 7) >= months[range[0]] && eventDate(t.date).slice(0, 7) <= months[range[1]]);
  const counts = months.map(month => timeline.filter(t => eventDate(t.date).startsWith(month) && (kind === 'all' || t.kind === kind)).length);
  const max = Math.max(1, ...counts);
  const key = (item: Profile['timeline'][number]) => `${item.kind}:${item.id}`;
  const event = filtered.find(t => key(t) === selected) || filtered[0];
  return <section className="panel ps-timeline-panel"><div className="section-title"><h2><CalendarDays size={17}/>业务时间轴</h2><span className="badge">{filtered.length} / {timeline.length} 项</span></div>
    <div className="ps-filter-row" aria-label="时间线类型筛选">{[['all', '全部'], ...Object.entries(eventLabels)].map(([id, label]) => <button key={id} aria-pressed={kind === id} onClick={() => setKind(id)}>{label}</button>)}</div>
    {months.length ? <><div className="ps-period-heading"><strong>{months[range[0]]} — {months[range[1]]}</strong><button className="p5-text-button" onClick={() => { setRange([0, months.length - 1]); setKind('all'); }}><RotateCcw size={12}/>重置范围</button></div>
    <div className="ps-histogram" aria-label="按月业务记录分布">{months.map((month, i) => <button key={month} aria-label={`${month}：${counts[i]} 项，查看该月`} aria-pressed={i >= range[0] && i <= range[1]} onClick={() => setRange([i, i])} title={`${month} · ${counts[i]} 项`}><span className="ps-bar-count">{counts[i]}</span><i style={{ height: `${Math.max(3, counts[i] / max * 70)}px` }}/><small>{month.slice(2)}</small></button>)}</div>
    <div className="ps-range-controls"><label>起始月份<input aria-label="时间轴起始月份" type="range" min={0} max={months.length - 1} value={range[0]} aria-valuetext={months[range[0]]} disabled={months.length < 2} onChange={e => setRange([Math.min(Number(e.target.value), range[1]), range[1]])}/></label><label>结束月份<input aria-label="时间轴结束月份" type="range" min={0} max={months.length - 1} value={range[1]} aria-valuetext={months[range[1]]} disabled={months.length < 2} onChange={e => setRange([range[0], Math.max(Number(e.target.value), range[0])])}/></label></div>
    <div className="ps-events-workspace"><ol className="ps-event-list">{filtered.map(item => <li key={key(item)}><button className={key(item) === (event && key(event)) ? 'active' : ''} onClick={() => setSelected(key(item))}><time>{eventDate(item.date)}</time><strong>{item.title}</strong><span>{eventLabels[item.kind] || item.kind}<i/>{statusLabels[item.status] || item.status}</span></button></li>)}</ol>{event ? <aside className="ps-event-detail"><small>{eventLabels[event.kind] || event.kind}</small><h3>{event.title}</h3><time>{eventDate(event.date)}</time><p>{event.detail}</p><span className="badge">{statusLabels[event.status] || event.status}</span><button className="btn btn-secondary" onClick={() => onEvidence(event)}><FileSearch size={14}/>查看关联依据</button></aside> : <div className="p5-empty">当前范围内没有此类记录，请调整月份或类型。</div>}</div></> : <div className="p5-empty"><CalendarDays size={30}/><strong>暂无业务记录</strong><p>关联停留、事实、笔录或文书后，时间轴会自动汇集记录。</p></div>}
    <p className="p5-caption ps-timeline-note">拖动月份滑块筛选范围，点击柱形查看单月。入出境按登记的发生日期，事实按事实日期（未填写时取创建日期），笔录及文书按创建时间，预警按最近更新时间。</p>
  </section>;
}
