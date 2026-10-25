import { useId, useState } from 'react';
import { useMediaQuery } from '../../hooks/useMediaQuery';
import { chartScale, seriesValue, type SeriesKey, type TrendDatum } from './chartMath';
const series = [{ key: 'entries', label: '入境', color: 'var(--primary)' }, { key: 'exits', label: '出境', color: 'var(--chart-secondary)' }, { key: 'net', label: '登记净流入', color: 'var(--chart-line)' }] as const;
export function TrendChart({ data, switchable = false, compact = false }: { data: TrendDatum[]; switchable?: boolean; compact?: boolean }) {
  const [hidden, setHidden] = useState<SeriesKey[]>([]), [style, setStyle] = useState<'combo'|'area'>('combo');
  const [selected, setSelected] = useState<number | null>(null), [table, setTable] = useState(false);
  const id = useId(), mobile = useMediaQuery('(max-width: 650px)');
  const labelCount = Math.min(data.length, mobile ? 4 : 7);
  const labelIndices = new Set(Array.from({length:labelCount}, (_,i) => Math.round(i*(data.length-1)/Math.max(1,labelCount-1))));
  const visible = series.filter(s => !hidden.includes(s.key)), scale = chartScale(data, visible.map(s => s.key));
  const y = (v: number) => 218 - (v - scale.min) / (scale.max - scale.min) * 186;
  const width = mobile ? 420 : compact ? 600 : 900, left = 48, right = width - 35;
  const step = (right-left) / Math.max(1, data.length), x = (i: number) => left + step * (i + .5), bar = Math.min(12, step * .28);
  const point = selected === null ? null : data[selected];
  const path = (key: SeriesKey) => data.map((d, i) => `${i ? 'L' : 'M'} ${x(i)} ${y(seriesValue(d, key))}`).join(' ');
  return <div className="business-trend">
    <div className="chart-toolbar"><div className="chart-series" aria-label="显示统计系列">{series.map(s => <button key={s.key} aria-pressed={!hidden.includes(s.key)} onClick={() => setHidden(v => v.includes(s.key) ? v.filter(k => k !== s.key) : [...v, s.key])}><i style={{ background: s.color }} className={s.key === 'net' ? 'line' : ''}/>{s.label}</button>)}</div>{switchable && <div className="chart-view-switch" aria-label="趋势图形切换">{(['combo','area'] as const).map(v => <button key={v} aria-pressed={style === v} onClick={() => setStyle(v)}>{v === 'combo' ? '柱线图' : '面积图'}</button>)}</div>}</div>
    {!data.length ? <div className="p5-empty">暂无登记趋势数据</div> : <><svg viewBox={`0 0 ${width} 254`} role="group" aria-label="出入境趋势图，左右方向键选择日期" tabIndex={0} onFocus={() => setSelected(v => v ?? 0)} onKeyDown={e => { if (['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) { e.preventDefault(); setSelected(v => e.key === 'Home' ? 0 : e.key === 'End' ? data.length-1 : Math.max(0, Math.min(data.length-1, (v ?? 0)+(e.key === 'ArrowRight' ? 1 : -1)))); } }}>
      <defs>{series.map(s => <linearGradient id={`${id}-${s.key}`} key={s.key} x1="0" y1="0" x2="0" y2="1"><stop stopColor={s.color} stopOpacity=".32"/><stop offset="1" stopColor={s.color} stopOpacity=".03"/></linearGradient>)}</defs>
      <text x="16" y="16">人次</text>{scale.ticks.map(t => <g key={t}><line x1={left} x2={right} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeDasharray={t === 0 ? undefined : '3 5'}/><text x={left-12} y={y(t)+4} textAnchor="end">{t}</text></g>)}
      {visible.map(s => style === 'combo' && s.key !== 'net' ? <g key={s.key} fill={s.color}>{data.map((d,i) => <rect key={i} x={x(i)+(s.key === 'entries' ? -bar-1 : 1)} y={y(seriesValue(d,s.key))} width={bar} height={Math.max(0,y(0)-y(seriesValue(d,s.key)))} rx={Math.min(3,bar/3)} />)}</g> : <g key={s.key}>{style === 'area' && s.key !== 'net' && <path d={`${path(s.key)} L ${x(data.length-1)} ${y(0)} L ${x(0)} ${y(0)} Z`} fill={`url(#${id}-${s.key})`}/>}<path d={path(s.key)} stroke={s.color} fill="none" strokeWidth="2.5" strokeLinejoin="round" strokeDasharray={s.key === 'net' ? '5 3' : undefined}/>{data.length <= 31 && data.map((d,i) => <circle key={i} cx={x(i)} cy={y(seriesValue(d,s.key))} r="3" fill="var(--surface)" stroke={s.color} strokeWidth="2"/>)}</g>)}
      {point && selected !== null && <line x1={x(selected)} x2={x(selected)} y1="28" y2="218" stroke="var(--muted)" strokeDasharray="3 4"/>}
      {data.map((d,i) => <g key={i}><rect x={left+i*step} y="28" width={step} height="190" fill="transparent" onPointerEnter={() => setSelected(i)} onClick={() => setSelected(i)}><title>{d.label}：入境 {d.entries}，出境 {d.exits}，净流入 {d.entries-d.exits}</title></rect>{labelIndices.has(i) && <text x={x(i)} y="243" textAnchor="middle">{d.label.length > 7 ? d.label.slice(5) : d.label}</text>}</g>)}
    </svg><div className="chart-readout"><p aria-live="polite">{point ? `${point.label} · 入境 ${point.entries} · 出境 ${point.exits} · 净流入 ${point.entries-point.exits}` : '悬停、轻触或用方向键查看每日数值'}{!visible.length && ' · 当前系列均已隐藏'}</p><button className="p5-text-button" aria-expanded={table} onClick={() => setTable(v => !v)}>{table ? '收起数据表' : '查看数据表'}</button></div><p className="chart-scope">净流入 = 登记入境 − 登记出境；均使用左侧人次刻度。</p>{table && <div className="p5-table-wrap"><table><thead><tr><th>日期</th><th>入境</th><th>出境</th><th>净流入</th></tr></thead><tbody>{data.map((d,i) => <tr key={i}><td>{d.label}</td><td>{d.entries}</td><td>{d.exits}</td><td>{d.entries-d.exits}</td></tr>)}</tbody></table></div>}</>}
  </div>;
}
