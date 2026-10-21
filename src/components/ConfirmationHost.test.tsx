import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ConfirmationHost } from './ConfirmationHost';
import { Dialog } from './Dialog';
import { confirmAction, getConfirmation } from '../lib/confirmation';
import { Choice } from './Choice';

beforeEach(() => { vi.spyOn(window, 'confirm').mockImplementation(() => { throw new Error('Native confirm must never be called'); }); vi.spyOn(window, 'alert').mockImplementation(() => { throw new Error('Native alert must never be called'); }); });

describe('custom confirmation decisions', () => {
  it('requires explicit approval; repeated requests never approve a second action', async () => {
    render(<ConfirmationHost/>); let result!: Promise<boolean>;
    act(() => { result = confirmAction('归档后仍保留原文。', { title: '归档记录？', confirmLabel: '归档' }); });
    expect(screen.getByRole('alertdialog')).toHaveAccessibleName('归档记录？');
    await expect(confirmAction('second request')).resolves.toBe(false);
    expect(screen.getByRole('button', { name: '取消' })).toHaveFocus();
    await userEvent.click(screen.getByRole('button', { name: '归档' }));
    await expect(result).resolves.toBe(true); expect(getConfirmation()).toBeNull();
    expect(window.confirm).not.toHaveBeenCalled();
  });
  it('Escape cancels and never runs the requested action', async () => {
    render(<ConfirmationHost/>); let result!: Promise<boolean>;
    act(() => { result = confirmAction('未保存的音频将丢失。', { confirmLabel: '丢弃录音', tone: 'danger' }); });
    await userEvent.keyboard('{Escape}'); await expect(result).resolves.toBe(false);
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });
  it('keeps the dirty editor, text and focus after cancelling a nested close', async () => {
    function Editor() {
      const [open, setOpen] = useState(true);
      return <>{open && <Dialog title="编辑资料" onClose={async () => { if (await confirmAction('内容尚未保存。', { confirmLabel: '放弃修改', cancelLabel: '继续编辑' })) setOpen(false); }}><input aria-label="资料名称" defaultValue="未保存的合成资料"/></Dialog>}<ConfirmationHost/></>;
    }
    render(<Editor/>);
    await userEvent.click(screen.getByRole('button', { name: '关闭弹窗' }));
    await userEvent.click(screen.getByRole('button', { name: '继续编辑' }));
    expect(screen.getByRole('dialog')).toHaveAccessibleName('编辑资料');
    expect(screen.getByRole('textbox')).toHaveValue('未保存的合成资料');
    await waitFor(() => expect(screen.getByRole('button', { name: '关闭弹窗' })).toHaveFocus());
    await userEvent.click(screen.getByRole('button', { name: '关闭弹窗' }));
    await userEvent.click(screen.getByRole('button', { name: '放弃修改' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
  it('displays field validation in an application dialog and focuses the field', async () => {
    const submit = vi.fn(event => event.preventDefault());
    render(<><form onSubmit={submit}><label><span>标题</span><input required aria-label="标题"/></label><button>保存</button></form><ConfirmationHost/></>);
    await userEvent.click(screen.getByRole('button', { name: '保存' }));
    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByRole('alertdialog')).toHaveTextContent('请填写标题');
    await userEvent.click(screen.getByRole('button', { name: '返回填写' }));
    await waitFor(() => expect(screen.getByRole('textbox')).toHaveFocus());
  });
  it('settles a pending request as cancelled if the host is removed', async () => {
    const { unmount } = render(<ConfirmationHost/>); let result!: Promise<boolean>;
    act(() => { result = confirmAction('pending'); }); unmount();
    await expect(result).resolves.toBe(false); expect(getConfirmation()).toBeNull();
  });
  it('uses a custom menu and preserves empty selections', async () => {
    const change = vi.fn(); render(<Choice value="a" onChange={change} aria-label="场景"><option value="">请选择</option><option value="a">场景 A</option><option value="b">场景 B</option></Choice>);
    const trigger = screen.getByRole('combobox'); trigger.focus();
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    expect(await screen.findByRole('listbox')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('option', { name: '请选择' }));
    expect(change).toHaveBeenCalledWith({ target: { value: '' } });
  });
});
