import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { chartScale, roseRadius, sectorPath } from './chartMath';
import { TrendChart } from './TrendChart';
import { DistributionChart } from './DistributionChart';

describe('true-data chart behaviour', () => {
  it('keeps negative net flow within a zero-based integer scale', () => {
    const rows = [{label:'10-24',entries:2,exits:9},{label:'10-25',entries:0,exits:0}];
    const scale = chartScale(rows,['entries','exits','net']);
    expect(scale.min).toBeLessThanOrEqual(-7); expect(scale.max).toBeGreaterThanOrEqual(9);
    expect(scale.ticks).toContain(0); expect(scale.ticks.every(Number.isInteger)).toBe(true);
    expect(chartScale(rows,[]).max).toBeGreaterThan(0);
  });
  it('supports touch/keyboard values, series toggles and an unchanged data table across styles', () => {
    render(<TrendChart switchable data={[{label:'2026-10-24',entries:2,exits:9},{label:'2026-10-25',entries:4,exits:0}]}/>);
    fireEvent.keyDown(screen.getByRole('group',{name:/出入境趋势图/}),{key:'Home'});
    expect(screen.getByText('2026-10-24 · 入境 2 · 出境 9 · 净流入 -7')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'查看数据表'}));
    fireEvent.click(screen.getByRole('button',{name:'面积图'}));
    fireEvent.click(screen.getByRole('button',{name:'登记净流入'}));
    expect(screen.getByRole('button',{name:'登记净流入'})).toHaveAttribute('aria-pressed','false');
    expect(within(screen.getByRole('table')).getByText('-7')).toBeInTheDocument();
  });
  it('preserves every category and full denominator through distribution switches', () => {
    const rows = Array.from({length:12},(_,i) => ({label:`分类${i+1}`,value:1}));
    render(<DistributionChart title="测试分布" data={rows}/>);
    for (const style of ['扇形','玫瑰','柱状','环形']) {
      fireEvent.click(screen.getByRole('button',{name:style}));
      expect(screen.getByText('当前存量 · 共 12 条')).toBeInTheDocument();
      expect(within(screen.getByLabelText('测试分布完整数据')).getAllByRole('button')).toHaveLength(12);
    }
    fireEvent.click(screen.getByRole('button',{name:/^分类12 /}));
    expect(screen.getByText('分类12 · 1 条 · 占全部 8.3%')).toBeInTheDocument();
  });
  it('uses area proportional roses and renders a one-category full circle', () => {
    expect(roseRadius(25,100,120)).toBe(60);
    expect(roseRadius(0,0,120)).toBe(0);
    expect(sectorPath(0,Math.PI*2,120).match(/ A /g)).toHaveLength(2);
    render(<DistributionChart title="空分布" data={[]}/>);
    expect(screen.getByText('暂无可统计的记录')).toBeInTheDocument();
  });
});
