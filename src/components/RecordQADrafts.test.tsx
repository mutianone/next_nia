import { render,screen,cleanup,waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach,expect,it,vi } from 'vitest';
import { RecordQADrafts } from './RecordQADrafts';
import { api } from '../api';
import type { RecordDetail } from '../record-types';
vi.mock('../api',()=>({api:vi.fn()}));
afterEach(()=>{cleanup();vi.resetAllMocks();});
const record={id:'r',revision:1,segments:[],audio_clips:[{id:'a',playback_url:'/audio'}]} as unknown as RecordDetail;
function material(){return {revision:1,history:[],sentence_history:[],items:[{recognition_run_id:'run',channel_id:0,speaker_id:0,business_role:'UNKNOWN',revision:0,confirmed_by:null,confirmed_at:null}],sentences:[0,1].map(i=>({id:`s${i}`,recognition_run_id:'run',text:i?'张某。':'你叫什么名字？',revision:0,audio_id:'a',start_ms:null,parts:[{start:0,end:i?3:7,role:'UNKNOWN',intent:'UNKNOWN'}]}))};}
async function open(){
 const data=material();
 vi.mocked(api).mockImplementation(async(path,init)=>{
   if(init?.method==='POST')return {id:'saved',revision:1} as never;
   return (path.endsWith('/speaker-mappings')?structuredClone(data):{items:[]}) as never;
 });
 render(<RecordQADrafts record={record} disabled={false} onPending={vi.fn()}/>);
 await userEvent.click(screen.getByRole('button',{name:'角色与 QA 草稿'}));
 await screen.findByLabelText('说话人映射1');
 await waitFor(()=>expect(screen.queryByRole('status')).not.toBeInTheDocument());
 return data;
}
it('keeps provider identities unknown until explicit scoped mapping; sends only accepted fields',async()=>{
 await open();expect(screen.getByLabelText('说话人映射1')).toHaveValue('UNKNOWN');
 await userEvent.selectOptions(screen.getByLabelText('说话人映射1'),'OFFICER');
 await waitFor(()=>expect(vi.mocked(api).mock.calls.some(c=>c[1]?.method==='POST')).toBe(true));
 const call=vi.mocked(api).mock.calls.find(c=>c[1]?.method==='POST')!;
 expect(JSON.parse(call[1]!.body as string)).toEqual({expected_revision:1,recognition_run_id:'run',channel_id:0,speaker_id:0,business_role:'OFFICER',mapping_revision:0});
});
it('preserves another unsaved sentence when saving one sentence',async()=>{
 await open();await userEvent.click(screen.getByText('逐句核对与拆分'));
 await userEvent.selectOptions(screen.getByLabelText('发言1第1段角色'),'OFFICER');
 await userEvent.selectOptions(screen.getByLabelText('发言2第1段角色'),'INTERVIEWEE');
 await userEvent.click(screen.getAllByRole('button',{name:'保存本句核对'})[0]);
 await waitFor(()=>expect(screen.queryByRole('status')).not.toBeInTheDocument());
 expect(screen.getByLabelText('发言2第1段角色')).toHaveValue('INTERVIEWEE');
 expect(screen.getByRole('button',{name:'构建 QA 草稿'})).toBeDisabled();
});
it('splits exact source text and clears role without fabricating time',async()=>{
 await open();await userEvent.click(screen.getByText('逐句核对与拆分'));
 const input=screen.getByLabelText('发言1第1段原文') as HTMLTextAreaElement;
 input.setSelectionRange(2,2);await userEvent.click(screen.getAllByRole('button',{name:'在光标处拆分'})[0]);
 expect(screen.getByLabelText('发言1第1段原文')).toHaveValue('你叫');
 expect(screen.getByLabelText('发言1第2段原文')).toHaveValue('什么名字？');
 await userEvent.click(screen.getAllByRole('button',{name:'保存本句核对'})[0]);
 const call=vi.mocked(api).mock.calls.find(c=>c[1]?.method==='POST')!;
 const body=JSON.parse(call[1]!.body as string);
 expect(body.parts).toEqual([{start:0,end:2,role:'UNKNOWN',intent:'UNKNOWN'},{start:2,end:7,role:'UNKNOWN',intent:'UNKNOWN'}]);
 expect(body).not.toHaveProperty('start_ms');
});
it('retains edits on server conflict and never calls formal segment or fill endpoints',async()=>{
 await open();await userEvent.click(screen.getByText('逐句核对与拆分'));
 await userEvent.selectOptions(screen.getByLabelText('发言1第1段角色'),'OFFICER');
 vi.mocked(api).mockRejectedValueOnce(new Error('版本冲突'));
 await userEvent.click(screen.getAllByRole('button',{name:'保存本句核对'})[0]);
 expect(await screen.findByRole('alert')).toHaveTextContent('版本冲突');
 expect(screen.getByLabelText('发言1第1段角色')).toHaveValue('OFFICER');
 expect(vi.mocked(api).mock.calls.every(c=>!c[0].endsWith('/segments')&&!c[0].endsWith('/analyze'))).toBe(true);
});
it('can discard unsaved reviews and close without writing',async()=>{
 await open();await userEvent.click(screen.getByText('逐句核对与拆分'));
 await userEvent.selectOptions(screen.getByLabelText('发言1第1段角色'),'OFFICER');
 await userEvent.click(screen.getByRole('button',{name:'撤销未保存修改'}));
 await waitFor(()=>expect(screen.queryByRole('status')).not.toBeInTheDocument());
 expect(screen.getByLabelText('发言1第1段角色')).toHaveValue('UNKNOWN');
 await userEvent.click(screen.getByRole('button',{name:/^关闭$/}));
 expect(vi.mocked(api).mock.calls.every(c=>!c[1]?.method)).toBe(true);
});
