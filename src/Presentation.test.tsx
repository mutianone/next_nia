import { render } from './test/auth';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Assistant } from './pages/Assistant';
import { Settings } from './pages/Settings';
vi.mock('./components/PageHeading', () => ({ PageHeading: ({title, children}: {title: string; children: React.ReactNode}) => <header><h1>{title}</h1>{children}</header> }));
const settings = {
  settings: {
    general: {system_name:'境智',organization:'出入境业务中心'},
    environment:{mode:'test',postgres:{host:'localhost',port:5432,database:'nia',username:'nia',password_configured:false}},
    ai:{enabled:false,default_provider:'siliconflow',providers:{siliconflow:{enabled:false,chat_model:'Qwen/Qwen3-8B',asr_model:'FunAudioLLM/SenseVoiceSmall',api_key_configured:false},deepseek:{enabled:false,chat_model:'deepseek-flash',api_key_configured:false}}}
  }, runtime:{active_environment:'test',database_engine:'SQLite',pending_restart:false,sample_workspace:true,local_processing:true}
};
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
describe('real execution in populated workspaces',()=>{
  it('requires configured credentials and consent even with a stale local_processing flag',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>settings})));
    render(<Assistant onKnowledge={vi.fn()} onSettings={vi.fn()}/>);
    fireEvent.click(screen.getByRole('button',{name:'AI 问答'}));
    await screen.findByText('请先配置 API 密钥，并启用 AI 和默认服务商。');
    fireEvent.change(screen.getByLabelText('法规问题'),{target:{value:'签证延期'}});
    expect(screen.getByRole('button',{name:'生成回答'})).toBeDisabled();
    expect(screen.getByRole('checkbox')).toBeInTheDocument();
  });
  it('keeps real API configuration and capability tests visible',async()=>{
    vi.stubGlobal('fetch',vi.fn(async(url:string)=>({ok:true,json:async()=>url.endsWith('/settings')?settings:{counts:{},events:[]}})));
    render(<Settings/>);
    await screen.findByText('测试版');
    fireEvent.click(screen.getByRole('tab',{name:/AI 服务/}));
    expect(await screen.findByText('AI 服务配置')).toBeInTheDocument();
    expect(screen.getAllByRole('button',{name:'实际生成测试'})).toHaveLength(2);
    expect(screen.getByRole('button',{name:'语音转写测试'})).toBeDisabled();
    expect(screen.queryByText('智能处理服务')).not.toBeInTheDocument();
  });
});
