import {render,screen,cleanup,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,it,expect,vi} from 'vitest';
import {api} from '../api';
import {RecordFillPreview} from './RecordFillPreview';
import type {RecordDetail} from '../record-types';
vi.mock('../api',()=>({api:vi.fn()}));
afterEach(()=>{cleanup();vi.resetAllMocks();});
const record={id:'r',revision:3,audio_clips:[]} as unknown as RecordDetail;
function batch(stale=false){return {id:'batch',stale,reason:stale?'来源已变化':null,fields:[{id:'q_name',label:'姓名',fillable:true,protected:false}],items:[{candidate_id:'c',revision:1,template_field_id:'q_name',question_text:'你叫什么名字？',answer_text:'金子，不是金枝。',match_method:'RULE',candidate_status:'PENDING',problems:[],formal_value:'原有人工文字',source_refs:[],review:null,reviews:[]}]};}
async function open(stale=false){
 vi.mocked(api).mockImplementation(async(_path,init)=>((init?.method==='POST'?batch(stale):{batch:batch(stale),batches:[]}) as never));
 render(<RecordFillPreview record={record} draft={{id:'d',revision:2}} disabled={false}/>);
 await userEvent.click(screen.getByText('模板匹配预览'));
 await screen.findByText('原答：金子，不是金枝。');
}
it('shows original QA and formal comparison; accepts only shadow candidate',async()=>{
 await open();expect(screen.getByText('正式字段原值：原有人工文字')).toBeInTheDocument();
 await userEvent.click(screen.getByRole('button',{name:'接受原答'}));
 const call=vi.mocked(api).mock.calls.find(c=>c[1]?.method==='POST')!;
 expect(call[0]).toBe('/records/r/fill-candidates/c/review');expect(JSON.parse(call[1]!.body as string)).toMatchObject({action:'ACCEPTED',candidate_revision:1,expected_revision:3});
});
it('blocks stale review and preserves original answer when editing',async()=>{
 await open(true);expect(screen.getByRole('button',{name:'接受原答'})).toBeDisabled();expect(screen.getByRole('button',{name:'保存人工修改'})).toBeDisabled();
});
it('requires explicit external consent for optional semantic suggestions',async()=>{
 await open();await userEvent.click(screen.getByLabelText('使用已配置模型提供匹配建议'));
 expect(screen.getByRole('button',{name:'生成匹配预览'})).toBeDisabled();
 await userEvent.click(screen.getByLabelText('同意发送本次问题及模板字段至已配置的云端服务'));
 await userEvent.click(screen.getByRole('button',{name:'生成匹配预览'}));
 await waitFor(()=>expect(vi.mocked(api).mock.calls.some(c=>c[0].endsWith('/fill-preview'))).toBe(true));
 const call=vi.mocked(api).mock.calls.find(c=>c[0].endsWith('/fill-preview'))!;
 expect(JSON.parse(call[1]!.body as string)).toMatchObject({draft_revision:2,use_semantic:true,confirmed:true});
});
it('retains the manually selected field when reviewing an unmatched candidate again',async()=>{
 const b=batch();const c={...b.items[0],template_field_id:null,review:{status:'ACCEPTED',template_field_id:'q_name',value:'金子。'}};
 vi.mocked(api).mockImplementation(async()=>({batch:{...b,items:[c]},batches:[]}) as never);
 render(<RecordFillPreview record={record} draft={{id:'d',revision:2}} disabled={false}/>);
 await userEvent.click(screen.getByText('模板匹配预览'));await screen.findByText('原答：金子，不是金枝。');
 await userEvent.click(screen.getByRole('button',{name:'保存人工修改'}));
 const call=vi.mocked(api).mock.calls.find(c=>c[1]?.method==='POST')!;
 expect(JSON.parse(call[1]!.body as string)).toMatchObject({template_field_id:'q_name',action:'MODIFIED'});
});
