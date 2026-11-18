import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { RecordDualReview } from './RecordDualReview';
import type { RecordDetail } from '../record-types';

afterEach(()=>{cleanup();vi.unstubAllGlobals();});

it('requires an explicit source for every group and listening confirmation before saving',async()=>{
  const record={id:'record',revision:2,segments:[],transcription_attempts:[{provider:'dual'}],audio_clips:[{id:'audio',playback_url:'/audio'}]} as unknown as RecordDetail;
  const groups=[{group:0,status:'conflict',errors:{},candidates:[
    {provider:'siliconflow',text:'不确定。',segments:[{text:'不确定。',start_ms:1000,end_ms:1800,speaker_id:'1'}]},
    {provider:'volcengine',text:'确定。',segments:[{text:'确定。',start_ms:1100,end_ms:1900,speaker_id:'1'}]}]},
    {group:1,status:'single',errors:{volcengine:{message:'服务暂不可用'}},candidates:[
      {provider:'siliconflow',text:'还有信息。',segments:[{text:'还有信息。',start_ms:3000,end_ms:4000}]}]}];
  const writes:unknown[]=[];const onApplied=vi.fn();const onPending=vi.fn();
  vi.stubGlobal('fetch',vi.fn(async(_url:string,options?:RequestInit)=>{
    if(options?.method==='POST'){writes.push(JSON.parse(String(options.body)));return {ok:true,json:async()=>record};}
    return {ok:true,json:async()=>({revision:3,runs:[{id:'run',audio_id:'audio',applied:true,groups}]})};
  }));
  render(<RecordDualReview record={record} disabled={false} onApplied={onApplied} onPending={onPending}/>);
  fireEvent.click(screen.getByRole('button',{name:'双引擎核对'}));
  const save=await screen.findByRole('button',{name:'保存所选版本，继续核对角色'});
  expect(save).toBeDisabled();
  expect(await screen.findByText('不确定。')).toBeVisible();
  expect(screen.getByText(/当前结果不构成双引擎一致/)).toBeVisible();
  fireEvent.click(screen.getByRole('button',{name:'回听片段 1'}));
  expect(document.querySelector('audio')).toHaveAttribute('src','/audio');
  fireEvent.click(screen.getByLabelText('片段 1 采用 Qwen3-ASR（硅基流动）'));
  fireEvent.click(screen.getByLabelText(/我已回听并核对/));expect(save).toBeDisabled();
  fireEvent.click(screen.getByLabelText('片段 2 采用 Qwen3-ASR（硅基流动）'));expect(save).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/我已回听并核对/));
  fireEvent.click(save);
  await waitFor(()=>expect(onApplied).toHaveBeenCalled());
  expect(writes).toEqual([{expected_revision:3,confirmed:true,choices:['siliconflow','siliconflow']}]);
  expect(onPending).toHaveBeenLastCalledWith(false);
});

it('preselects recommendations but still requires confirmation and permits changing the choice',async()=>{
  const record={id:'record',revision:2,segments:[],transcription_attempts:[{provider:'dual'}],audio_clips:[]} as unknown as RecordDetail;
  const group={group:0,status:'text_agrees',errors:{},recommendation:{provider:'siliconflow',method:'rules',reason:'问答边界更完整'},candidates:[
    {provider:'siliconflow',text:'我看过。',segments:[{text:'我看过。'}]},
    {provider:'volcengine',text:'我看过。',segments:[{text:'我看过。'}]}]};
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({revision:2,runs:[{id:'run',audio_id:'audio',applied:true,groups:[group]}]})})));
  render(<RecordDualReview record={record} disabled={false} onApplied={vi.fn()} onPending={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button',{name:'双引擎核对'}));
  expect(await screen.findByLabelText('片段 1 采用 Qwen3-ASR（硅基流动）')).toBeChecked();
  expect(screen.getByText(/自动预选：Qwen/)).toHaveTextContent('问答边界更完整');
  const save=screen.getByRole('button',{name:'保存所选版本，继续核对角色'});
  expect(save).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/我已回听并核对/));expect(save).toBeEnabled();
  fireEvent.click(screen.getByLabelText('片段 1 采用 火山豆包 2.0'));expect(save).toBeDisabled();
});
