import { render, screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { App } from './App';
import { ConfirmationHost } from './components/ConfirmationHost';

const settings = {
  general: { system_name: '合成验收平台', organization: '合成资料中心' },
  environment: { mode: 'test', postgres: { host: 'localhost', port: 5432, database: 'test', username: 'test', password_configured: false } },
  ai: { enabled: false, default_provider: 'siliconflow', providers: {
    siliconflow: { enabled: true, base_url: 'https://api.siliconflow.cn/v1', chat_model: 'test-model', asr_model: 'test-asr', api_key_configured: false },
    deepseek: { enabled: false, base_url: 'https://api.deepseek.com', chat_model: 'test-model', api_key_configured: false },
  } },
};
beforeEach(() => {
  window.history.replaceState(null, '', '#/settings');
  vi.spyOn(window, 'confirm').mockImplementation(() => { throw new Error('Native confirm forbidden'); });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith('/maintenance') ? { counts: {}, events: [], environment: 'test' } : url.endsWith('/health') ? { status: 'ok', version: '0.5.0', stage: 5, active_environment: 'test', database_engine: 'SQLite' } : { settings, runtime: { active_environment: 'test', database_engine: 'SQLite', pending_restart: false } }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
});
afterEach(() => vi.unstubAllGlobals());

it('keeps the settings draft and URL on cancel, then navigates only after approval', async () => {
  render(<><App/><ConfirmationHost/></>);
  const name = await screen.findByDisplayValue('合成验收平台');
  await userEvent.type(name, '修改');
  await userEvent.click(screen.getByRole('button', { name: '外观与主题' }));
  expect(screen.getByRole('alertdialog')).toHaveAccessibleName('离开前，要保留修改吗？');
  expect(window.location.hash).toBe('#/settings');
  await userEvent.click(screen.getByRole('button', { name: '继续编辑' }));
  expect(screen.getByDisplayValue('合成验收平台修改')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '外观与主题' }));
  await userEvent.click(screen.getByRole('button', { name: '放弃修改并离开' }));
  await waitFor(() => expect(window.location.hash).toBe('#/appearance'));
  expect(window.confirm).not.toHaveBeenCalled();
});

it('rolls back hash navigation while a custom confirmation is pending', async () => {
  render(<><App/><ConfirmationHost/></>);
  const name = await screen.findByDisplayValue('合成验收平台');
  await userEvent.type(name, '修改');
  act(() => { window.history.pushState(null, '', '#/records'); window.dispatchEvent(new HashChangeEvent('hashchange')); });
  expect(window.location.hash).toBe('#/settings');
  await userEvent.keyboard('{Escape}');
  expect(screen.getByDisplayValue('合成验收平台修改')).toBeInTheDocument();
  expect(window.location.hash).toBe('#/settings');
});

it('preserves migration text when switching settings tabs and hides the inactive panel', async () => {
  render(<><App/><ConfirmationHost/></>);
  await screen.findByDisplayValue('合成验收平台');
  await userEvent.click(screen.getByRole('tab', { name: /数据与维护/ }));
  const draft = screen.getByRole('textbox', { name: '迁移 JSON' });
  await userEvent.type(draft, 'pending draft');
  await userEvent.click(screen.getByRole('tab', { name: /基础信息/ }));
  expect(screen.queryByRole('textbox', { name: '迁移 JSON' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('tab', { name: /数据与维护/ }));
  expect(screen.getByRole('textbox', { name: '迁移 JSON' })).toHaveValue('pending draft');
});
