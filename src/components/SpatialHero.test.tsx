import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { SpatialHero } from './SpatialHero';
import { SpatialExperience } from './spatial/SpatialExperience';

const mock = vi.hoisted(() => ({ update: vi.fn(), point: vi.fn(), turn: vi.fn(), reset: vi.fn(), dispose: vi.fn(), create: vi.fn() }));
vi.mock('./spatial/scene', () => ({ createSpatialScene: mock.create }));
vi.mock('./spatial/officerScene', () => ({ createOfficerScene: mock.create }));
beforeEach(() => {
  vi.clearAllMocks();
  mock.create.mockImplementation(() => mock);
  vi.stubGlobal('WebGL2RenderingContext', function MockWebGL() {});
});
afterEach(() => vi.unstubAllGlobals());

it('keeps the business action available when 3D initialization fails', async () => {
  mock.create.mockImplementation(() => { throw new Error('WebGL unavailable'); });
  const action = vi.fn(); render(<SpatialHero variant="dashboard" action={<button onClick={action}>配置我的工作空间</button>}/>);
  expect(await screen.findByText('静态展示 · 业务功能正常可用')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '配置我的工作空间' }));
  expect(action).toHaveBeenCalledOnce();
  expect(screen.getByRole('button', { name: '重置三维视角' })).toBeDisabled();
});

it('supports keyboard rotation, explicit expansion, pause and reset', async () => {
  render(<SpatialHero variant="records"/>);
  const stage = screen.getByRole('group', { name: '询问笔录工作室三维场景' });
  await waitFor(() => expect(stage).toHaveAttribute('data-scene-state', 'ready'));
  stage.focus(); await userEvent.keyboard('{ArrowRight}{ArrowUp}{Home}');
  expect(mock.turn).toHaveBeenNthCalledWith(1, .15, 0); expect(mock.turn).toHaveBeenNthCalledWith(2, 0, -.08); expect(mock.reset).toHaveBeenCalledOnce();
  await userEvent.click(screen.getByRole('button', { name: '展开' }));
  expect(mock.update).toHaveBeenLastCalledWith(expect.objectContaining({ expanded: true }));
  await userEvent.click(screen.getByRole('button', { name: '暂停场景动效' }));
  expect(mock.update).toHaveBeenLastCalledWith(expect.objectContaining({ animated: false }));
  expect(screen.getByRole('button', { name: '播放场景动效' })).toHaveAttribute('aria-pressed', 'true');
  await userEvent.click(screen.getByRole('button', { name: '重置三维视角' }));
  expect(screen.getByRole('button', { name: '聚合' })).toHaveAttribute('aria-pressed', 'true');
});

it('honors reduced motion while retaining deliberate manual interaction', async () => {
  render(<SpatialExperience theme="jade" reducedMotion page="knowledge"><SpatialHero variant="knowledge"/></SpatialExperience>);
  await waitFor(() => expect(mock.create).toHaveBeenCalledOnce());
  expect(mock.create.mock.calls[0][2]).toMatchObject({ animated: false, accent: '#b3be91' });
  expect(screen.getByRole('button', { name: '暂停场景动效' })).toBeDisabled();
  const stage = screen.getByRole('group', { name: '法规知识中心三维场景' });
  fireEvent.keyDown(stage, { key: 'ArrowLeft' }); expect(mock.turn).toHaveBeenCalledWith(-.15, 0);
});

it('releases the scene on page departure and does not initialize after unmount', async () => {
  const first = render(<SpatialHero variant="people"/>);
  await waitFor(() => expect(mock.create).toHaveBeenCalledOnce()); first.unmount(); expect(mock.dispose).toHaveBeenCalledOnce();
  mock.create.mockClear(); const second = render(<SpatialHero variant="alerts"/>); second.unmount();
  await act(async () => { await Promise.resolve(); }); expect(mock.create).not.toHaveBeenCalled();
});

it('updates the local palette and falls back safely after a lost context', async () => {
  const { rerender } = render(<SpatialExperience theme="government" reducedMotion={false} page="appearance"><SpatialHero variant="appearance"/></SpatialExperience>);
  await waitFor(() => expect(mock.create).toHaveBeenCalledOnce());
  rerender(<SpatialExperience theme="aurora" reducedMotion={false} page="appearance"><SpatialHero variant="appearance"/></SpatialExperience>);
  expect(mock.update).toHaveBeenLastCalledWith(expect.objectContaining({ accent: '#c1a1df', primary: '#7060b3' }));
  act(() => mock.create.mock.calls[0][3]());
  expect(mock.dispose).toHaveBeenCalledOnce(); expect(screen.getByText('静态展示 · 业务功能正常可用')).toBeInTheDocument();
});

it('uses a portrait character view on the dashboard and keeps full-body framing explicit', async () => {
  render(<SpatialHero variant="dashboard"/>);
  await waitFor(() => expect(mock.create).toHaveBeenCalledOnce());
  expect(mock.create.mock.calls[0][1]).toMatchObject({ expanded: true });
  expect(screen.getByRole('button', { name: '半身' })).toHaveAttribute('aria-pressed', 'true');
  await userEvent.click(screen.getByRole('button', { name: '全身' }));
  expect(mock.update).toHaveBeenLastCalledWith(expect.objectContaining({ expanded: false }));
  await userEvent.click(screen.getByRole('button', { name: '重置三维视角' }));
  expect(screen.getByRole('button', { name: '半身' })).toHaveAttribute('aria-pressed', 'true');
});
