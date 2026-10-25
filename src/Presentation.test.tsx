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
    ai:{external_permissions:{question:true,asr:true,analysis:true},enabled:false,default_provider:'siliconflow',providers:{siliconflow:{enabled:false,chat_model:'Qwen/Qwen3-8B',asr_model:'FunAudioLLM/SenseVoiceSmall',api_key_configured:false},deepseek:{enabled:false,chat_model:'deepseek-flash',api_key_configured:false}}}
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
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });
  it('keeps real API configuration and capability tests visible',async()=>{
    vi.stubGlobal('fetch',vi.fn(async(url:string)=>({ok:true,json:async()=>url.endsWith('/settings')?settings:url.includes('/knowledge?')?{items:[],total:0}:{counts:{},events:[]}})));
    render(<Settings/>);
    await screen.findByText('测试版');
    fireEvent.click(screen.getByRole('tab',{name:/AI 服务/}));
    expect(await screen.findByText('AI 服务配置')).toBeInTheDocument();
    expect(screen.getAllByRole('button',{name:'实际生成测试'})).toHaveLength(2);
    expect(screen.getByRole('button',{name:'语音转写测试'})).toBeDisabled();
    expect(screen.queryByText('智能处理服务')).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox',{name:/允许法规问答/})).toBeChecked();
    expect(screen.getByRole('checkbox',{name:/允许录音转写/})).toBeChecked();
    expect(screen.getByRole('checkbox',{name:/允许笔录摘要/})).toBeChecked();
  });
});

it('blocks questions when central permission is off and directs users to settings', async () => {
  const configured = structuredClone(settings);
  configured.settings.ai.enabled = true;
  configured.settings.ai.providers.siliconflow.enabled = true;
  configured.settings.ai.providers.siliconflow.api_key_configured = true;
  configured.settings.ai.external_permissions.question = false;
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>configured})));
  const openSettings = vi.fn();
  render(<Assistant onKnowledge={vi.fn()} onSettings={openSettings}/>);
  fireEvent.click(screen.getByRole('button',{name:'AI 问答'}));
  await screen.findByText(/勾选“允许法规问答发送至服务商”/);
  fireEvent.change(screen.getByLabelText('法规问题'),{target:{value:'签证延期'}});
  expect(screen.getByRole('button',{name:'生成回答'})).toBeDisabled();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'前往系统配置'}));
  expect(openSettings).toHaveBeenCalledOnce();
});
