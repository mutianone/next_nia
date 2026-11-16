import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { SpatialHero } from './SpatialHero';
it('retains business content and actions without rendering 3D scenes', async () => {
  const action = vi.fn();
  const { container } = render(<SpatialHero variant="dashboard" title="业务概览" action={<button onClick={action}>前往核查</button>}/>);
  expect(screen.getByText('业务概览')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '前往核查' }));
  expect(action).toHaveBeenCalledOnce();
  expect(container.querySelector('canvas, .spatial-stage, .spatial-visual, .spatial-toolbar')).toBeNull();
});
