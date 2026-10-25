import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { beijingInput, calendarDays, InterviewDateTime, InterviewLocation } from './InterviewFields';

describe('interview fields', () => {
  it('uses Beijing dates across midnight and handles leap years', () => {
    expect(beijingInput('2026-10-24T20:30:00Z')).toBe('2026-10-25T04:30');
    expect(calendarDays('2024-02')).toEqual({ offset: 3, count: 29 });
    expect(calendarDays('2025-02').count).toBe(28);
  });
  it('keeps changes pending until confirmed and emits timezone explicitly', () => {
    const change = vi.fn();
    render(<InterviewDateTime value="2026-10-25T09:30:00+08:00" onChange={change}/>);
    fireEvent.click(screen.getByRole('button', { name: '选择询问发生时间' }));
    fireEvent.click(screen.getByRole('button', { name: '2026-10-20' }));
    expect(change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /^取消$/ }));
    expect(change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '选择询问发生时间' }));
    fireEvent.click(screen.getByRole('button', { name: '2026-10-21' }));
    fireEvent.click(screen.getByRole('button', { name: '确认时间' }));
    expect(change).toHaveBeenCalledWith('2026-10-21T09:30:00+08:00');
  });
  it('highlights today, supports clearing, and accepts custom locations', () => {
    const change = vi.fn();
    render(<><InterviewDateTime value={null} onChange={change}/><InterviewLocation value="原询问室" locations={['第一询问室']} onChange={change}/></>);
    fireEvent.click(screen.getByRole('button', { name: '选择询问发生时间' }));
    fireEvent.click(screen.getByRole('button', { name: /^今天$/ }));
    expect(document.querySelector('[aria-current="date"]')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: '清空时间' }));
    expect(change).toHaveBeenCalledWith(null);
    fireEvent.change(screen.getByRole('textbox', { name: '询问地点' }), { target: { value: '现场询问地点' } });
    expect(change).toHaveBeenCalledWith('现场询问地点');
  });
});
