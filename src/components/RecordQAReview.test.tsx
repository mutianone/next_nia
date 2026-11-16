import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { RecordQAReview } from './RecordQAReview';
import { api } from '../api';
import { confirmAction } from '../lib/confirmation';
import type { RecordDetail } from '../record-types';
vi.mock('../api',()=>({api:vi.fn()}));
vi.mock('../lib/confirmation',()=>({confirmAction:vi.fn()}));
afterEach(()=>{cleanup();vi.resetAllMocks();});
const record={id:'r1',revision:2,segments:[{id:'s1',text:'听明白了吗？听明白了。'}],audio_clips:[{id:'a1',playback_url:'/audio'}]} as RecordDetail;
const turn={source_id:'s1',source_start:0,source_end:7,text:'听明白了吗？',suggested_role:'interviewer',speaker:'unknown',audio_id:'a1',listen_start_ms:null,start_ms:null,end_ms:null};
async function open(){
 vi.mocked(confirmAction).mockResolvedValue(true);
 vi.mocked(api).mockResolvedValue({revision:2,turns:[turn,{...turn,text:'听明白了。',suggested_role:'interviewee'}],issues:[]});
 const applied=vi.fn();render(<RecordQAReview record={record} disabled={false} onApplied={applied} onPending={vi.fn()}/>);
 await userEvent.click(screen.getByRole('button',{name:'整理问答'}));
 await screen.findByLabelText('发言1角色');return applied;
}
it('never accepts suggested roles automatically and saves only after manual selection',async()=>{
 const applied=await open();const save=screen.getByRole('button',{name:'确认并保存修订'});
 expect(save).toBeDisabled();expect(screen.getByLabelText('发言1角色')).toHaveValue('unknown');
 await userEvent.selectOptions(screen.getByLabelText('发言1角色'),'interviewer');
 expect(save).toBeDisabled();await userEvent.selectOptions(screen.getByLabelText('发言2角色'),'interviewee');
 vi.mocked(api).mockResolvedValue({...record,revision:3});await userEvent.click(save);
 await waitFor(()=>expect(applied).toHaveBeenCalled());
 const request=vi.mocked(api).mock.calls.at(-1)!;const body=JSON.parse(request[1]!.body as string);
 expect(body.expected_revision).toBe(2);expect(body.segments[0].text).toBe('问：听明白了吗？');
 expect(body.segments[1].text).toBe('答：听明白了。');expect(body.segments[1].id).toBeUndefined();
 expect(body.segments.every((s:{start_ms:number|null})=>s.start_ms===null)).toBe(true);
});
it('offers audio playback without fabricated timestamps and allows cancel without saving',async()=>{
 await open();await userEvent.click(screen.getAllByRole('button',{name:'回听对应原音'})[0]);
 expect(document.querySelector('audio')).toHaveAttribute('src','/audio');
 await userEvent.click(screen.getByRole('button',{name:'取消'}));expect(api).toHaveBeenCalledTimes(1);
});
it('retains uncommitted edits when save conflicts',async()=>{
 await open();await userEvent.selectOptions(screen.getByLabelText('发言1角色'),'interviewer');await userEvent.selectOptions(screen.getByLabelText('发言2角色'),'interviewee');
 vi.mocked(api).mockRejectedValue(new Error('版本冲突'));
 await userEvent.click(screen.getByRole('button',{name:'确认并保存修订'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('版本冲突');expect(screen.getByLabelText('发言2文字')).toHaveValue('听明白了。');
});

it('confirms one acoustic group once without merging identities from other audio',async()=>{
 vi.mocked(confirmAction).mockResolvedValue(true);
 vi.mocked(api).mockResolvedValue({revision:2,issues:[],turns:[{...turn,text:'[说话人 1] 第一句。',voice_group:'a1:1'}, {...turn,text:'[说话人 1] 第二句。',voice_group:'a1:1'}, {...turn,text:'[说话人 1] 另一录音。',voice_group:'a2:1'}]});
 render(<RecordQAReview record={record} disabled={false} onApplied={vi.fn()} onPending={vi.fn()}/>);
 await userEvent.click(screen.getByRole('button',{name:'整理问答'}));
 await userEvent.selectOptions(await screen.findByLabelText('声音1角色'),'interviewer');
 expect(screen.getByLabelText('发言1角色')).toHaveValue('interviewer');expect(screen.getByLabelText('发言2角色')).toHaveValue('interviewer');
 expect(screen.getByLabelText('发言3角色')).toHaveValue('unknown');
});

it('keeps supplementary transcription as a candidate until explicitly adopted',async()=>{
 vi.mocked(confirmAction).mockResolvedValue(true);
 vi.mocked(api).mockResolvedValueOnce({revision:2,issues:[],turns:[{...turn,text:'不申请回',start_ms:100,end_ms:2000}]}).mockResolvedValueOnce({text:'不申请回避。',start_ms:0,end_ms:2500});
 render(<RecordQAReview record={record} disabled={false} onApplied={vi.fn()} onPending={vi.fn()}/>);
 await userEvent.click(screen.getByRole('button',{name:'整理问答'}));
 await userEvent.click(await screen.findByRole('button',{name:'原音补转写'}));
 await screen.findByText('不申请回避。');expect(screen.getByLabelText('发言1文字')).toHaveValue('不申请回');
 await userEvent.click(screen.getByRole('button',{name:'采用候选'}));expect(screen.getByLabelText('发言1文字')).toHaveValue('不申请回避。');
 expect(screen.getByLabelText('发言1角色')).toHaveValue('unknown');
});

it('bulk adoption requires confirmation and does not save or overwrite manual roles',async()=>{
 await open();
 await userEvent.selectOptions(screen.getByLabelText('发言1角色'),'interpreter');
 vi.mocked(confirmAction).mockResolvedValueOnce(false);
 await userEvent.click(screen.getByRole('button',{name:'采用已核对的角色建议'}));
 expect(screen.getByLabelText('发言2角色')).toHaveValue('unknown');
 vi.mocked(confirmAction).mockResolvedValueOnce(true);
 await userEvent.click(screen.getByRole('button',{name:'采用已核对的角色建议'}));
 expect(screen.getByLabelText('发言1角色')).toHaveValue('interpreter');
 expect(screen.getByLabelText('发言2角色')).toHaveValue('interviewee');
 expect(api).toHaveBeenCalledTimes(1);
});
