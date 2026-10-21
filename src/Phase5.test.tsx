import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { Maintenance } from './components/Maintenance';
import { Profiles } from './pages/Profiles';
import { ConfirmationHost } from './components/ConfirmationHost';
import { api } from './api';
vi.mock('./api',()=>({api:vi.fn()}));
vi.mock('./components/SpatialHero',()=>({SpatialHero:()=>null}));
const person={id:'p1',name:'合成测试甲',code:'TEST001',nationality:'测试国',purpose:'测试学习',verification:'pending',source:'合成来源',archived:false,stays:[]};
const profile={person,origins:[],facts:[],relations:[],sources:[],timeline:[]};
beforeEach(()=>{vi.clearAllMocks();vi.spyOn(window,'confirm').mockImplementation(()=>{throw new Error('No native confirmation');});vi.mocked(api).mockImplementation(async(path)=>{if(path==='/maintenance')return {counts:{},events:[],environment:'test'};if(path.startsWith('/people'))return{items:[person],total:1};if(path==='/profiles/p1')return profile;if(path==='/migration/preview')return{total:1,ready:1,skipped:0,errors:[],can_import:true,preview_hash:'a'.repeat(64)};return{};});});
it('invalidates the migration approval whenever the reviewed JSON changes',async()=>{
  render(<Maintenance/>);
  fireEvent.change(screen.getByLabelText('迁移 JSON'),{target:{value:'{"source_key":"test","items":[]}'}});
  await userEvent.click(screen.getByRole('button',{name:'校验当前批次'}));
  const commit=await screen.findByRole('button',{name:'核对完成，确认导入'});expect(commit).toBeEnabled();
  fireEvent.change(screen.getByLabelText('迁移 JSON'),{target:{value:'{"source_key":"changed","items":[]}'}});
  expect(screen.queryByRole('button',{name:'核对完成，确认导入'})).not.toBeInTheDocument();
  expect(vi.mocked(api).mock.calls.some(([path])=>path==='/migration/commit')).toBe(false);
});
it('reports malformed migration JSON locally and never submits it',async()=>{
  render(<Maintenance/>);fireEvent.change(screen.getByLabelText('迁移 JSON'),{target:{value:'not-json'}});
  await userEvent.click(screen.getByRole('button',{name:'校验当前批次'}));expect(await screen.findByRole('alert')).toHaveTextContent('JSON 格式不正确');
  expect(vi.mocked(api).mock.calls.some(([path])=>path==='/migration/preview')).toBe(false);
});
it('requires evidence and a review note before a fact may be saved as verified',async()=>{
  render(<Profiles/>);await userEvent.click(await screen.findByRole('button',{name:'查看画像 合成测试甲'}));
  await userEvent.click(await screen.findByRole('button',{name:'登记事实'}));
  fireEvent.change(screen.getByLabelText('事实标签'),{target:{value:'来华事由'}});
  fireEvent.change(screen.getByLabelText('事实内容'),{target:{value:'合成陈述'}});
  fireEvent.change(screen.getByLabelText('来源说明'),{target:{value:'合成材料第1页'}});
  await userEvent.click(screen.getByLabelText('已人工核验'));await userEvent.click(screen.getByRole('button',{name:'保存记录'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('核验说明');
  expect(vi.mocked(api).mock.calls.some(([path])=>path.endsWith('/facts'))).toBe(false);
  fireEvent.change(screen.getByLabelText('核验说明（必填）'),{target:{value:'人工逐项核对'}});
  await userEvent.click(screen.getByRole('button',{name:'保存记录'}));
  await waitFor(()=>expect(vi.mocked(api)).toHaveBeenCalledWith('/profiles/p1/facts',expect.objectContaining({method:'POST'})));
});
it('uses the custom confirmation to preserve a fact draft when closing is cancelled',async()=>{
  render(<><Profiles/><ConfirmationHost/></>);await userEvent.click(await screen.findByRole('button',{name:'查看画像 合成测试甲'}));await userEvent.click(await screen.findByRole('button',{name:'登记事实'}));
  fireEvent.change(screen.getByLabelText('事实标签'),{target:{value:'尚未保存'}});await userEvent.click(screen.getByRole('button',{name:'取消'}));
  expect(screen.getByRole('alertdialog')).toBeInTheDocument();await userEvent.click(screen.getByRole('button',{name:'继续编辑'}));
  expect(screen.getByDisplayValue('尚未保存')).toBeInTheDocument();expect(window.confirm).not.toHaveBeenCalled();
});
