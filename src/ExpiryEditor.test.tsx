import { render } from './test/auth';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { it, expect, vi, afterEach } from 'vitest';
import { api } from './api';
import { DecisionEditor } from './pages/Alerts';
vi.mock('./api',()=>({api:vi.fn()}));
afterEach(()=>vi.resetAllMocks());
it('edits missing fields in the document body and saves with revision protection',async()=>{
 const doc={id:'doc',revision:1,fields:{person_name:'测试人',evidence:''},labels:{person_name:'姓名',evidence:'证据'},paragraphs:[],layout:{page:{},paragraphs:[{style:{},runs:[{kind:'field',key:'person_name',text:'　',style:{}},{kind:'field',key:'evidence',text:'　',style:{}}]}]},missing:['evidence']};
 vi.mocked(api).mockImplementation(async(_path,options)=> options?.method==='PUT'?{...doc,revision:2,fields:{person_name:'测试人',evidence:'补充证据'}}:doc);
 render(<DecisionEditor alertId="alert1" onClose={()=>{}}/>);
 expect(await screen.findByRole('textbox',{name:'姓名'})).toHaveValue('测试人');
 await userEvent.type(screen.getByRole('textbox',{name:'证据'}),'补充证据');
 expect(screen.getByRole('button',{name:'下载 DOCX'})).toBeDisabled();
 await userEvent.click(screen.getByRole('button',{name:'保存修改'}));
 await waitFor(()=>expect(screen.getByRole('button',{name:'下载 DOCX'})).toBeEnabled());
 expect(api).toHaveBeenLastCalledWith('/alerts/alert1/decision',{method:'PUT',body:JSON.stringify({expected_revision:1,fields:{person_name:'测试人',evidence:'补充证据'}})});
});

it('allows typing into empty signatures and date placeholders without collapsing the field',async()=>{
 const names={organization:'办案单位',officer:'承办人',approver:'批准人',document_date:'文书日期',deadline:'离境日期'};
 const fields=Object.fromEntries(Object.keys(names).map(k=>[k,'']));
 const doc={id:'doc',revision:1,fields,labels:names,paragraphs:[],layout:{page:{},paragraphs:[{style:{textAlign:'right'},runs:Object.entries(names).map(([key])=>({kind:'field',key,text:key.includes('date')||key==='deadline'?'20XX年XX月XX日':'　'.repeat(10),style:{}}))}]},missing:Object.keys(names)};
 vi.mocked(api).mockResolvedValue(doc);
 render(<DecisionEditor alertId="alert2" onClose={()=>{}}/>);
 for(const [key,label] of Object.entries(names)){
  const input=await screen.findByRole('textbox',{name:label});
  expect(input.tagName).toBe('TEXTAREA');
  await userEvent.click(input);
  await userEvent.type(input,key==='deadline'||key==='document_date'?'2026年11月10日':'测试填写');
  expect(input).toHaveValue(key==='deadline'||key==='document_date'?'2026年11月10日':'测试填写');
 }
 expect(screen.getByRole('button',{name:'保存修改'})).toBeEnabled();
});
