import {render,screen,cleanup,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {it,expect,afterEach,vi} from 'vitest';
import {api} from '../api';
import {RecordFillApply} from './RecordFillApply';
import type {RecordDetail} from '../record-types';
vi.mock('../api',()=>({api:vi.fn()}));
afterEach(()=>{cleanup();vi.resetAllMocks();});
const record={id:'r',revision:1} as RecordDetail;
const plan={fill_batch_id:'b',candidate_ids:['c'],expected_record_revision:1,expected_template_revision:'t',expected_field_revisions:{name:1},expected_candidate_revisions:{c:2},differences:[{field_id:'name',before:'旧值',after:'金子',locked:false}]};
function mock(enabled=true){vi.mocked(api).mockImplementation(async(path)=>path.endsWith('/history')?{record_structured_fill_apply_enabled:enabled,items:[]} as never:path.endsWith('/preview')?plan as never:record as never);}
function show(){return render(<RecordFillApply record={record} batchId="b" candidates={['c']} disabled={false}/>);}
it('hides formal entry while backend feature is off',async()=>{
 mock(false);show();await waitFor(()=>expect(api).toHaveBeenCalled());expect(screen.queryByText('预览正式应用差异')).not.toBeInTheDocument();
});
it('requires a second explicit authorization and submits reviewed IDs and versions only',async()=>{
 mock();show();await userEvent.click(await screen.findByText('预览正式应用差异'));
 expect(await screen.findByText('旧值')).toBeInTheDocument();expect(screen.getByText('金子')).toBeInTheDocument();
 expect(screen.getByText('确认应用到正式笔录')).toBeDisabled();
 await userEvent.click(screen.getByLabelText('已核对差异，授权本次正式应用'));await userEvent.click(screen.getByText('确认应用到正式笔录'));
 const c=vi.mocked(api).mock.calls.find(c=>c[0].endsWith('/fill-apply'))!;const data=JSON.parse(c[1]!.body as string);
 expect(data).toMatchObject({candidate_ids:['c'],expected_field_revisions:{name:1},expected_candidate_revisions:{c:2},confirmed:true});
 expect(data).not.toHaveProperty('differences');expect(data).not.toHaveProperty('value');expect(data.idempotency_key).toBeTruthy();
});
it('retains the same idempotency key after a lost response',async()=>{
 mock();let count=0;const original=vi.mocked(api).getMockImplementation()!;
 vi.mocked(api).mockImplementation(async(path,options)=>{if(path.endsWith('/fill-apply')&&count++===0)throw new Error('连接中断');return original(path,options);});
 show();await userEvent.click(await screen.findByText('预览正式应用差异'));await screen.findByText('旧值');
 await userEvent.click(screen.getByLabelText('已核对差异，授权本次正式应用'));await userEvent.click(screen.getByText('确认应用到正式笔录'));
 await screen.findByText('连接中断');await userEvent.click(screen.getByText('重试同一请求'));
 await waitFor(()=>expect(vi.mocked(api).mock.calls.filter(c=>c[0].endsWith('/fill-apply'))).toHaveLength(2));
 const calls=vi.mocked(api).mock.calls.filter(c=>c[0].endsWith('/fill-apply'));expect(calls[0][1]?.body).toEqual(calls[1][1]?.body);
});
it('clears stale confirmation after a version conflict',async()=>{
 mock();const original=vi.mocked(api).getMockImplementation()!;
 vi.mocked(api).mockImplementation(async(path,options)=>{if(path.endsWith('/fill-apply'))throw Object.assign(new Error('字段版本冲突'),{status:409});return original(path,options);});
 show();await userEvent.click(await screen.findByText('预览正式应用差异'));await screen.findByText('旧值');
 await userEvent.click(screen.getByLabelText('已核对差异，授权本次正式应用'));await userEvent.click(screen.getByText('确认应用到正式笔录'));
 await screen.findByText('字段版本冲突');expect(screen.queryByText('确认应用到正式笔录')).not.toBeInTheDocument();expect(screen.queryByText('重试同一请求')).not.toBeInTheDocument();
});
it('requires restore reason and separate confirmation',async()=>{
 const {fill_batch_id:_,candidate_ids:__,expected_candidate_revisions:___,...restore}=plan;
 vi.mocked(api).mockResolvedValue({record_structured_fill_apply_enabled:true,items:[{application_id:'a',operation:'APPLY',applied_at:'2026-11-16',applied_by:'synthetic',differences:plan.differences,restore_preview:restore,restore_error:null}]} as never);
 show();await userEvent.click(await screen.findByText('预览恢复'));expect(screen.getByText('确认恢复字段')).toBeDisabled();
 await userEvent.type(screen.getByLabelText('恢复原因'),'合成验收');await userEvent.click(screen.getByLabelText('已核对差异，授权本次恢复'));
 await userEvent.click(screen.getByText('确认恢复字段'));
 const call=vi.mocked(api).mock.calls.find(c=>c[0].endsWith('/a/restore'))!;expect(JSON.parse(call[1]!.body as string)).toMatchObject({reason:'合成验收',confirmed:true});
});
