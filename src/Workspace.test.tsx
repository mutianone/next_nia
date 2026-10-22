import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { PageHeading } from './components/PageHeading';
import { Knowledge } from './pages/Knowledge';
import { Documents } from './pages/Documents';
import { api } from './api';

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
  expect(screen.getByRole('dialog', { name: '文书编辑与预览' })).toBeInTheDocument();
});
