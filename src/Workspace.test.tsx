import { render } from './test/auth';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { PageHeading } from './components/PageHeading';
import { Knowledge } from './pages/Knowledge';
import { Documents } from './pages/Documents';
import { api } from './api';
import { ConfirmationHost } from './components/ConfirmationHost';
import { useListPosition, useViewState } from './hooks/useWorkspaceView';
import { formatDateTime, formatDuration } from './lib/display';

vi.mock('./api', () => ({ api: vi.fn() }));
vi.mock('./components/SpatialHero', () => ({ SpatialHero: () => <div>可交互三维场景</div> }));
beforeEach(() => vi.clearAllMocks());

it('opens the optional scene without blocking business actions and restores focus on close', async () => {
  const create = vi.fn();
  render(<PageHeading title="文书中心" description="管理草稿" scene="documents"><button onClick={create}>制作文书</button></PageHeading>);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '制作文书' }));
  expect(create).toHaveBeenCalledOnce();
  const view = screen.getByRole('button', { name: '查看文书中心三维视图' });
  await userEvent.click(view);
  expect(screen.getByRole('dialog', { name: '文书中心 · 三维视图' })).toBeInTheDocument();
  await userEvent.keyboard('{Escape}');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await waitFor(() => expect(view).toHaveFocus());
});

it('switches the catalogue from article search to a selected publication state', async () => {
  vi.mocked(api).mockResolvedValue({ items: [], total: 0, stats: { documents: 5, published: 3, drafts: 1, chunks: 20 }, retrieval: { mode: 'lexical', message: '本地检索' } });
  render(<Knowledge onAsk={vi.fn()}/>);
  await screen.findByRole('button', { name: /待审核\s*1/ });
  await userEvent.click(screen.getByRole('tab', { name: '条款检索' }));
  await userEvent.click(screen.getByRole('button', { name: /待审核\s*1/ }));
  expect(screen.getByRole('tab', { name: '资料库' })).toHaveAttribute('aria-selected', 'true');
  await waitFor(() => expect(vi.mocked(api).mock.calls.some(([path]) => path.startsWith('/knowledge?') && new URLSearchParams(path.split('?')[1]).get('status') === 'draft')).toBe(true));
  expect(screen.getByRole('button', { name: /待审核\s*1/ })).toHaveAttribute('aria-pressed', 'true');
  await userEvent.click(await screen.findByRole('button', { name: '清除筛选' }));
  await waitFor(() => expect(screen.getByRole('button', { name: /全部资料\s*5/ })).toHaveAttribute('aria-pressed', 'true'));
});

it('opens the selected register document, including from a filtered list', async () => {
  const document = { id: 'doc-2', title: '停留材料核对记录', person_name: '林某', person_code: 'P002', template_name: '内部工作单', template_version: '1.0', template_kind: 'internal', status: 'reviewed', revision: 2, updated_at: '2026-10-22T01:00:00Z', fields: {}, template_snapshot: { body: '留存正文' }, unresolved_fields: [], history: [], rendered_text: '已核对正文', review_note: '' };
  vi.mocked(api).mockImplementation(async path => {
    if (path === '/documents/doc-2') return document;
    if (path.startsWith('/documents?')) return { items: [document], total: 1, stats: { total: 1, drafts: 0, reviewed: 1, archived: 0 } };
    return { items: [] };
  });
  render(<Documents/>);
  await screen.findByRole('button', { name: '打开文书 停留材料核对记录' });
  await userEvent.click(screen.getByRole('button', { name: '已核对' }));
  await waitFor(() => expect(vi.mocked(api).mock.calls.some(([path]) => path.includes('status=reviewed'))).toBe(true));
  await userEvent.click(await screen.findByRole('button', { name: '打开文书 停留材料核对记录' }));
  expect(await screen.findByText('已核对正文')).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByRole('region', { name: '文书编辑与预览' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '返回文书列表' }));
  const row = await screen.findByRole('button', { name: '打开文书 停留材料核对记录' });
  expect(row).toHaveAttribute('data-selected', 'true');
  await waitFor(() => expect(row).toHaveFocus());
  expect(screen.getByRole('button', { name: '已核对' })).toHaveClass('active');
});


it('keeps an unsaved document in its workspace until discard is explicitly confirmed', async () => {
  const doc = { id: 'draft-1', title: '待补充文书', status: 'draft', revision: 1, template_kind: 'internal', fields: {}, unresolved_fields: ['facts'], history: [], template_snapshot: { body: '{{facts}}' }, rendered_text: '{{facts}}', review_note: '' };
  vi.mocked(api).mockImplementation(async path => path === '/documents/draft-1' ? doc : path.startsWith('/documents?') ? { items: [doc], total: 1, stats: {} } : { items: [] });
  render(<><Documents/><ConfirmationHost/></>);
  expect(await screen.findByText('待补齐：核查事实')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '打开文书 待补充文书' }));
  await userEvent.type(await screen.findByLabelText('文书标题 *'), '修改');
  await userEvent.click(screen.getByRole('button', { name: '返回文书列表' }));
  expect(screen.getByRole('alertdialog')).toHaveAccessibleName('保留这次修改？');
  await userEvent.click(screen.getByRole('button', { name: '继续编辑' }));
  expect(screen.getByLabelText('文书标题 *')).toHaveValue('待补充文书修改');
  await userEvent.click(screen.getByRole('button', { name: '返回文书列表' }));
  await userEvent.click(screen.getByRole('button', { name: '放弃修改' }));
  expect(await screen.findByRole('button', { name: '打开文书 待补充文书' })).toBeInTheDocument();
  expect(vi.mocked(api).mock.calls.every(([, init]) => !init?.method)).toBe(true);
});

it('shows concrete missing review metadata, then opens the knowledge workspace', async () => {
  const doc = { id: 'law-1', title: '业务指引', status: 'draft', source: '', effective_from: null, effective_to: null, version: '1', chunks: [], size_bytes: 100, chunk_count: 0 };
  vi.mocked(api).mockImplementation(async path => path === '/knowledge/law-1' ? doc : { items: [doc], total: 1, stats: { documents: 1, published: 0, drafts: 1, chunks: 0 }, retrieval: { message: '本地检索' } });
  render(<Knowledge onAsk={vi.fn()}/>);
  const missing = await screen.findByText('待补充：资料来源、施行日期；随后核对原文');
  await userEvent.click(missing.closest('button')!);
  expect(await screen.findByRole('region', { name: '法规原文与版本' })).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '返回法规列表' }));
  expect(await screen.findByText('待补充：资料来源、施行日期；随后核对原文')).toBeInTheDocument();
});

it('restores a register filter, selected row and scroll position after leaving the page', async () => {
  function Register() {
    const [query, setQuery] = useViewState('test.query', '');
    const view = useListPosition('test.register', true, true);
    return <><input aria-label="筛选" value={query} onChange={e => setQuery(e.target.value)}/><button data-work-row="row-7" data-selected={view.selectedRow==='row-7'} onClick={() => view.remember('row-7')}>条目七</button></>;
  }
  const first = render(<Register/>);
  await userEvent.type(screen.getByLabelText('筛选'), '林');
  Object.defineProperty(window, 'scrollY', { configurable: true, value: 620 });
  fireEvent.scroll(window);
  await userEvent.click(screen.getByRole('button', { name: '条目七' }));
  first.unmount();
  Object.defineProperty(window, 'scrollY', { configurable: true, value: 0 });
  render(<Register/>);
  expect(screen.getByLabelText('筛选')).toHaveValue('林');
  expect(screen.getByRole('button', { name: '条目七' })).toHaveAttribute('data-selected', 'true');
  await waitFor(() => expect(window.scrollTo).toHaveBeenCalledWith({ top: 620, behavior: 'instant' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '条目七' })).toHaveFocus());
});

it('uses Shanghai minute precision across dates and does not wrap long recording durations', () => {
  expect(formatDateTime('2026-10-21T17:05:00Z')).toBe('2026-10-22 01:05');
  expect(formatDateTime(null)).toBe('未记录');
  expect(formatDateTime('invalid')).toBe('日期无效');
  expect(formatDuration(3661000)).toBe('01:01:01');
  expect(formatDuration(90000000)).toBe('25:00:00');
});
