import { useState } from 'react';
import { roseRadius, sectorPath } from './chartMath';
export type Group = { label: string; value: number };
const colors = ['var(--primary)', 'var(--chart-secondary)', '#8d78b5', '#c38e49', '#4e9c8c', '#c77988', '#6b8ba8', '#9e9864'];
type View = 'donut' | 'pie' | 'rose' | 'bar';
export function DistributionChart({ title, data, caption = '当前存量', initial = 'donut' }: { title: string; data: Group[]; caption?: string; initial?: View }) {
  const [view, setView] = useState<View>(initial), [selected, setSelected] = useState<string | null>(null);
  const rows = data.filter(d => d.value > 0), total = rows.reduce((s,d) => s+d.value,0), max = Math.max(0,...rows.map(d => d.value));
  const active = rows.find(d => d.label === selected);
  let angle = -Math.PI/2;
  return <section className="panel distribution-panel"><div className="section-title"><div><h2>{title}</h2><p className="p5-caption">{caption} · 共 {total.toLocaleString()} 条</p></div></div>
    <div className="chart-view-switch" role="group" aria-label={`${title}图形切换`}>{([{id:'donut',name:'环形'},{id:'pie',name:'扇形'},{id:'rose',name:'玫瑰'},{id:'bar',name:'柱状'}] as const).map(v => <button key={v.id} aria-pressed={view === v.id} onClick={() => setView(v.id)}>{v.name}</button>)}</div>
    {!total ? <div className="p5-empty"><p>暂无可统计的记录</p></div> : <><div className={`distribution-body ${view === 'bar' ? 'is-bar' : ''}`}>
      {view !== 'bar' && <svg viewBox="0 0 300 300" role="img" aria-label={`${title}${view === 'rose' ? '玫瑰图，扇区面积表示数量' : '占比分布图'}，全部数值见右侧列表`}>
        {view === 'rose' && [40,80,120].map(r => <circle key={r} cx="150" cy="150" r={r} fill="none" stroke="var(--border)" strokeDasharray="3 4"/>)}
        {rows.map((row,i) => { const sweep = view === 'rose' ? 2*Math.PI/rows.length : row.value/total*2*Math.PI; const start = angle; angle += sweep; return <path key={row.label} d={sectorPath(start,sweep,view === 'rose' ? roseRadius(row.value,max,124) : 124)} fill={colors[i%colors.length]} opacity={selected && selected !== row.label ? .35 : 1} stroke="var(--surface)" strokeWidth="2" onPointerEnter={() => setSelected(row.label)} onClick={() => setSelected(row.label)}><title>{row.label}：{row.value} 条，占 {(row.value/total*100).toFixed(1)}%</title></path>; })}
        {view === 'donut' && <><circle cx="150" cy="150" r="86" fill="var(--surface)"/><text className="distribution-total" x="150" y="146" textAnchor="middle">{active?.value ?? total}</text><text x="150" y="172" textAnchor="middle">{active ? '所选分类数量' : '全部分类总量'}</text></>}
      </svg>}
      <div className="distribution-legend" aria-label={`${title}完整数据`}>{rows.map((row,i) => <button key={row.label} aria-label={`${row.label} ${row.value} 条 占 ${(row.value/total*100).toFixed(1)}%`} aria-pressed={selected === row.label} onClick={() => setSelected(v => v === row.label ? null : row.label)}><span className="distribution-label"><i style={{ background:colors[i%colors.length] }}/><span>{row.label}</span></span><strong>{row.value}</strong><small>{(row.value/total*100).toFixed(1)}%</small>{view === 'bar' && <span className="distribution-bar"><i style={{ width:`${row.value/max*100}%`,background:colors[i%colors.length] }}/></span>}</button>)}</div>
    </div><div className="chart-readout" aria-live="polite"><p>{active ? `${active.label} · ${active.value} 条 · 占全部 ${(active.value/total*100).toFixed(1)}%` : `完整展示 ${rows.length} 个有记录分类`}</p>{active && <button className="p5-text-button" onClick={() => setSelected(null)}>取消选择</button>}</div><p className="chart-scope">{view === 'rose' ? '等角玫瑰图：扇区面积对应数量，半径按数量平方根计算。' : '占比分母为全部分类总量；切换图形不改变数据范围。'}</p></>}
  </section>;
}
