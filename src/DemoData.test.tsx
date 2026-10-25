import { render } from './test/auth';
import { screen,waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach,expect,it,vi } from 'vitest';
import { DemoDataTools } from './components/DemoDataTools';
import { ConfirmationHost } from './components/ConfirmationHost';
import { api } from './api';
vi.mock('./api',()=>({api:vi.fn()}));
const empty={enabled:true,active:false,batch_id:null,created_at:null,counts:{}};
const ready={...empty,active:true,batch_id:'b1',counts:{people:36,record_sessions:12}};
beforeEach(()=>{vi.clearAllMocks();vi.spyOn(window,'confirm').mockImplementation(()=>{throw new Error('Native dialog forbidden');});vi.mocked(api).mockResolvedValue(empty);});
it('generates only after custom confirmation and disables repeat generation',async()=>{
  vi.mocked(api).mockImplementation(async path=>path.endsWith('/generate')?ready:empty);
  const changed=vi.fn();render(<><DemoDataTools onChange={changed}/><ConfirmationHost/></>);
  const button=screen.getByRole('button',{name:'一键生成演示资料'});await waitFor(()=>expect(button).toBeEnabled());await userEvent.click(button);
  expect(screen.getByRole('alertdialog')).toHaveAccessibleName('准备完整演示资料');
  expect(api).toHaveBeenCalledTimes(1);await userEvent.click(screen.getByRole('button',{name:'一键生成'}));
  await waitFor(()=>expect(changed).toHaveBeenCalledTimes(1));expect(button).toBeDisabled();expect(window.confirm).not.toHaveBeenCalled();
});
it('cancelling cleanup preserves the data and never calls the mutation endpoint',async()=>{
  vi.mocked(api).mockResolvedValue(ready);render(<><DemoDataTools onChange={()=>{}}/><ConfirmationHost/></>);
  const button=screen.getByRole('button',{name:'清理演示资料'});await waitFor(()=>expect(button).toBeEnabled());await userEvent.click(button);
  expect(screen.getByRole('alertdialog')).toHaveTextContent('无法撤销');await userEvent.keyboard('{Escape}');
  expect(vi.mocked(api).mock.calls.some(([p])=>p.endsWith('/clear'))).toBe(false);
});
it('cleanup binds the displayed batch and updates the view on success',async()=>{
  vi.mocked(api).mockImplementation(async path=>path.endsWith('/clear')?empty:ready);render(<><DemoDataTools onChange={()=>{}}/><ConfirmationHost/></>);
  const button=screen.getByRole('button',{name:'清理演示资料'});await waitFor(()=>expect(button).toBeEnabled());await userEvent.click(button);await userEvent.click(screen.getByRole('button',{name:'确认清理'}));
  await waitFor(()=>expect(api).toHaveBeenCalledWith('/demo-data/clear',{method:'POST',body:JSON.stringify({confirmed:true,batch_id:'b1'})}));
  await waitFor(()=>expect(button).toBeDisabled());
});
it('production mode exposes no enabled generation or cleanup action',async()=>{
  vi.mocked(api).mockResolvedValue({...ready,enabled:false});render(<DemoDataTools onChange={()=>{}}/>);
  await screen.findByText('资料已就绪');expect(screen.getByRole('button',{name:'一键生成演示资料'})).toBeDisabled();expect(screen.getByRole('button',{name:'清理演示资料'})).toBeDisabled();
});
