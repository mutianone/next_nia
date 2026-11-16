import { render, screen, waitFor, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { RecordCandidates } from './RecordCandidates';
import { api } from '../api';
import { confirmAction } from '../lib/confirmation';
import type { RecordDetail, FieldCandidate } from '../record-types';
vi.mock('../api',()=>({api:vi.fn()}));
vi.mock('../lib/confirmation',()=>({confirmAction:vi.fn()}));
afterEach(()=>{cleanup();vi.resetAllMocks();});
const candidate:FieldCandidate={id:'c1',field_id:'address',value:'新陈述地址',previous_value:'此前地址',current_value:'人工地址',protected:true,status:'conflict',latest:true,origin:'ai',created_at:'2026-11-03T12:00:00Z',sources:[],reviews:[]};
const row={id:'r1',revision:4,candidates:[candidate],template_snapshot:{name:'模板',fields:[{id:'address',label:'现住址'}]}} as RecordDetail;
const props=()=>({record:row,canReview:true,busy:false,onBusy:vi.fn(),onApply:vi.fn(),onError:vi.fn(),onPlay:vi.fn()});
it('requires explicit replacement before applying a protected candidate',async()=>{
 const p=props();render(<RecordCandidates {...p}/>);vi.mocked(confirmAction).mockResolvedValue(false);
 await userEvent.click(screen.getByRole('button',{name:'采用并保护'}));expect(api).not.toHaveBeenCalled();
 vi.mocked(confirmAction).mockResolvedValue(true);vi.mocked(api).mockResolvedValue({...row,revision:5});
 await userEvent.type(screen.getByRole('textbox',{name:'本次审核说明（可选）'}),'已回听');
 await userEvent.click(screen.getByRole('button',{name:'采用并保护'}));
 await waitFor(()=>expect(p.onApply).toHaveBeenCalled());
 expect(api).toHaveBeenCalledWith('/records/r1/candidates/c1/review',{method:'POST',body:JSON.stringify({expected_revision:4,decision:'confirmed',note:'已回听',replace_protected:true})});
});
it('shows source precision honestly and replays from the beginning when no timestamp exists',async()=>{
 const p=props();render(<RecordCandidates {...p}/>);
 vi.mocked(api).mockResolvedValue({stale:false,sources:[{segment_id:'s1',precision:'segment',quote:'合成原文',audio_id:'a1',start_ms:null}]});
 await userEvent.click(screen.getByRole('button',{name:'查看原文依据'}));
 expect(await screen.findByText('关联段落（需人工核对候选含义）')).toBeInTheDocument();
 await userEvent.click(screen.getByRole('button',{name:'从原音开头回听'}));expect(p.onPlay).toHaveBeenCalledWith('a1',0);
});
it('keeps review disabled for archived or read-only sessions',()=>{
 render(<RecordCandidates {...props()} canReview={false}/>);
 expect(screen.getByRole('button',{name:'采用并保护'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'驳回'})).toBeDisabled();
});

it('shows exact answer evidence and blocked missing fields without applying them',async()=>{
 const record={...row,candidates:[{...candidate,evidence_preview:[{question:'住在哪里？',quote:'新陈述地址',precision:'exact'}]}],template_snapshot:{...row.template_snapshot!,fields:[{id:'address',label:'现住址'},{id:'q002',label:'告知书回答'}],quality_checks:[{field_id:'q002',code:'missing_evidence',message:'缺少逐字问答依据',status:'blocked'}]}};
 render(<RecordCandidates {...props()} record={record}/>);
 expect(screen.getByText('问：住在哪里？')).toBeInTheDocument();
 expect(screen.getByText('答：新陈述地址')).toBeInTheDocument();
 await userEvent.click(screen.getByText('未回填与校验提示 · 1'));
 expect(screen.getByText('缺少逐字问答依据')).toBeVisible();
 expect(api).not.toHaveBeenCalled();
});
