import { render,screen,waitFor,cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach,expect,it,vi } from 'vitest';
import { RecordTemplateManager } from './RecordTemplateManager';
import { RecordVersionCompare } from './RecordVersionCompare';
import { api } from '../api';
import type { RecordDetail } from '../record-types';
vi.mock('../api',()=>({api:vi.fn()}));
afterEach(()=>{cleanup();vi.resetAllMocks();});
it('keeps the new question open while editing and saves clean aliases',async()=>{
 vi.mocked(api).mockImplementation(async(_url,options)=>options?.method==='PUT'?{id:'custom',config_revision:1,...JSON.parse(String(options.body))}:{items:[]});
 render(<RecordTemplateManager/>);await userEvent.click(screen.getByRole('button',{name:'新建模板'}));
 await userEvent.type(screen.getByRole('textbox',{name:'模板名称'}),'合成模板');
 await userEvent.type(screen.getByRole('textbox',{name:'问题或字段名称'}),'你住在哪里？');
 expect(screen.getByRole('textbox',{name:'问题别名（每行一个）'})).toBeVisible();
 await userEvent.type(screen.getByRole('textbox',{name:'问题别名（每行一个）'}),'你的现住址？\n');
 await userEvent.click(screen.getByRole('button',{name:'保存模板新版本'}));
 await waitFor(()=>expect(api).toHaveBeenCalledWith(expect.stringMatching(/^\/record-templates\/custom_/),expect.objectContaining({method:'PUT'})));
 const options=vi.mocked(api).mock.calls.find(([,o])=>o?.method==='PUT')![1]!;
 const body=JSON.parse(String(options.body));expect(body.fields[0].question_aliases).toEqual(['你的现住址？']);
});
it('compares saved field values without changing either snapshot',async()=>{
 vi.mocked(api).mockImplementation(async url=>({snapshot:{template_snapshot:{fields:[{id:'q1',label:'现住址'}],values:{q1:String(url).endsWith('/1')?'原合成地址':'修订合成地址'}}}}));
 const record={id:'r',history:[{revision:1,created_at:'2026-11-03T00:00:00Z'},{revision:2,created_at:'2026-11-04T00:00:00Z'}]} as RecordDetail;
 render(<RecordVersionCompare record={record} onClose={()=>{}}/>);
 expect(await screen.findByText('原合成地址')).toBeInTheDocument();expect(screen.getByText('修订合成地址')).toBeInTheDocument();
 expect(vi.mocked(api).mock.calls.every(([,options])=>!options?.method || options.method==='GET')).toBe(true);
});
