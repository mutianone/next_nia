import type { CSSProperties } from 'react';
import { ChartNoAxesCombined, ShieldCheck } from 'lucide-react';
import type { Fact } from '../../pages/Profiles';
export const factKinds = { identity: '身份事实', purpose: '来华事由', residence: '停居留事实', other: '其他事实' };
export function FactDistribution({ facts, filter, onFilter }: { facts: Fact[]; filter: string; onFilter: (value: string) => void }) {
  const active = facts.filter(f => !f.archived), verified = active.filter(f => f.verified).length;
  const max = Math.max(1, ...Object.keys(factKinds).map(kind => active.filter(f => f.kind === kind).length));
  return <div className="ps-fact-overview"><div className="ps-verification-summary"><div className="ps-donut" style={{ '--verified': `${active.length ? verified / active.length * 100 : 0}%` } as CSSProperties}><span><strong>{verified}<small>/{active.length}</small></strong><small>事实已核验</small></span></div><div><h3><ShieldCheck size={15}/>事实核验进度</h3><p>{active.length - verified} 条待核验 · {facts.length - active.length} 条已归档</p><small>依据人工核验记录统计</small></div></div><div className="ps-fact-bars"><div className="ps-fact-chart-title"><h3><ChartNoAxesCombined size={15}/>事实构成</h3><button className="p5-text-button" onClick={() => onFilter('all')}>全部 {facts.length}</button></div>{Object.entries(factKinds).map(([kind, label]) => { const rows = active.filter(f => f.kind === kind); return <button key={kind} aria-pressed={filter === kind} aria-label={`筛选${label}，${rows.length}条`} onClick={() => onFilter(filter === kind ? 'all' : kind)}><span>{label}</span><i><b style={{ width: `${rows.length / max * 100}%` }}/></i><strong>{rows.length}</strong></button>; })}</div></div>;
}
