import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ProfileCandidates } from './ProfileCandidates';
vi.mock('../Dialog',()=>({Dialog:({children}:{children: React.ReactNode})=><section>{children}</section>}));
const candidate={analysis_id:'analysis-1',fact_index:0,record_title:'询问记录',record_revision:3,provider:'deepseek',model:'deepseek-flash',label:'会议地点',value:'二楼会议室',imported:false,sources:[{segment_id:'seg-1',text:'会议安排在二楼会议室。'}]};
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it('requires explicit selection and preserves pending verification after registration',async()=>{
  const fetcher=vi.fn(async (_url:string,init?:RequestInit)=>({ok:true,json:async()=>init?.method==='POST'?{id:'fact-1',verified:false}:{items:[candidate]}}));
  vi.stubGlobal('fetch',fetcher);
  const changed=vi.fn();
  render(<ProfileCandidates personId="person-1" onClose={vi.fn()} onChanged={changed}/>);
  const register=await screen.findByRole('button',{name:'登记为待核验事实'});
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(screen.getByText('会议安排在二楼会议室。')).toBeInTheDocument();
  fireEvent.click(register);
  expect(await screen.findByRole('button',{name:'已登记为事实'})).toBeDisabled();
  expect(changed).toHaveBeenCalledOnce();
  expect(JSON.parse(fetcher.mock.calls[1][1]!.body as string)).toEqual({analysis_id:'analysis-1',fact_index:0});
});
it('keeps stale-source failures visible without reporting registration',async()=>{
  vi.stubGlobal('fetch',vi.fn(async (_url:string,init?:RequestInit)=>({ok:!init?.method,status:409,json:async()=>init?.method?{detail:'笔录或摘要已变化'}:{items:[candidate]}})));
  const changed=vi.fn();
  render(<ProfileCandidates personId="person-1" onClose={vi.fn()} onChanged={changed}/>);
  fireEvent.click(await screen.findByRole('button',{name:'登记为待核验事实'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('笔录或摘要已变化');
  expect(changed).not.toHaveBeenCalled();
});
