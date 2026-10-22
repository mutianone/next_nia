import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import { FactDistribution } from './FactDistribution';
import { ProfileTimeline, eventDate } from './ProfileTimeline';
import { groupRelations, graphPosition } from './profileGraph';
import type { Fact, Relation } from '../../pages/Profiles';
import { vi } from 'vitest';
const relation = (id: string, props: Partial<Relation> = {}): Relation => ({ id, person_id: 'p1', target_id: 'p2', label: '已登记关系', source: '资料第2页', verified: true, review_note: '已逐项核对', archived: false, revision: 1, other: { id: 'p2', name: '乙', code: 'P002', archived: false }, ...props });
it('only includes verified active relations by default and groups multiple records for the same person', () => {
  const data = [relation('a'), relation('b'), relation('pending', { verified: false, other: { id: 'p3', name: '丙', code: 'P003', archived: false } }), relation('archived', { archived: true }), relation('archived-person', { other: { id: 'p4', name: '丁', code: 'P004', archived: true } })];
  expect(groupRelations(data, false).map(([id, rows]) => [id, rows.length])).toEqual([['p2', 2]]);
  expect(groupRelations(data, true).map(([id]) => id)).toEqual(['p2', 'p3']);
  expect(data).toHaveLength(5);
});
it('places every node beyond the old eight-node limit without overlapping positions', () => {
  for (const layout of ['orbit', 'columns'] as const) {
    const positions = Array.from({ length: 36 }, (_, i) => graphPosition(i, 36, layout));
    expect(new Set(positions.map(p => `${p.x},${p.y}`)).size).toBe(36);
    expect(positions.every(p => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true);
  }
});
it('filters the timeline by month and type, clamps crossed range handles, and opens the selected evidence', async () => {
  const onEvidence = vi.fn();
  const timeline = [
    { id: '1', kind: 'stay', date: '2026-01-10', title: '登记入境事实', detail: '入境凭证', status: '已登记' },
    { id: '2', kind: 'record', date: '2026-03-15T02:00:00Z', title: '来华事由笔录', detail: '版本 1', status: 'reviewed' },
    { id: '3', kind: 'fact', date: '2026-04-20', title: '学习事由', detail: '材料编号3', status: '待核验' },
  ];
  render(<ProfileTimeline timeline={timeline} onEvidence={onEvidence}/>);
  expect(screen.getByRole('button', { name: '2026-02：0 项，查看该月' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '2026-03：1 项，查看该月' }));
  expect(screen.queryByRole('button', { name: /登记入境事实/ })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '查看关联依据' }));
  expect(onEvidence).toHaveBeenCalledWith(timeline[1]);
  fireEvent.change(screen.getByRole('slider', { name: '时间轴起始月份' }), { target: { value: '3' } });
  expect(screen.getByRole('slider', { name: '时间轴起始月份' })).toHaveValue('2');
  await userEvent.click(screen.getByRole('button', { name: '出入境' }));
  expect(screen.getByText('当前范围内没有此类记录，请调整月份或类型。')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '重置范围' }));
  expect(screen.getByRole('button', { name: /登记入境事实/ })).toBeInTheDocument();
});
it('uses Shanghai dates at month boundaries but preserves date-only facts', () => {
  expect(eventDate('2026-01-31T20:00:00Z')).toBe('2026-02-01 04:00');
  expect(eventDate('2026-01-31')).toBe('2026-01-31');
});
it('excludes archived facts from verification counts and links chart filters to fact categories', async () => {
  const onFilter = vi.fn();
  const facts = [{ id: '1', kind: 'identity', verified: true, archived: false }, { id: '2', kind: 'purpose', verified: false, archived: false }, { id: '3', kind: 'purpose', verified: true, archived: true }] as Fact[];
  render(<FactDistribution facts={facts} filter="all" onFilter={onFilter}/>);
  expect(screen.getByText('1 条待核验 · 1 条已归档')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '筛选来华事由，1条' }));
  expect(onFilter).toHaveBeenCalledWith('purpose');
});
it('handles a profile without any timeline events', () => {
  render(<ProfileTimeline timeline={[]} onEvidence={() => {}}/>);
  expect(screen.getByText('暂无业务记录')).toBeInTheDocument();
  expect(screen.queryByRole('slider')).not.toBeInTheDocument();
});
