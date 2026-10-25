import { render } from './test/auth';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Assistant } from './pages/Assistant';
import { Settings } from './pages/Settings';

vi.mock('./components/PageHeading', () => ({ PageHeading: ({title, children}: {title: string; children: React.ReactNode}) => <header><h1>{title}</h1>{children}</header> }));
vi.mock('./components/ProcessingSteps', () => ({ ProcessingSteps: () => <div role="status">正在整理</div>, withPresentationPacing: (request: Promise<unknown>) => request }));
const settings = {
  settings: {
    general: {system_name:'境智', organization:'出入境业务中心'},
    environment: {mode:'test', postgres:{host:'localhost',port:5432,database:'nia',username:'nia',password_configured:false}},
    ai: {enabled:false,default_provider:'siliconflow',providers:{
      siliconflow:{enabled:false,chat_model:'',asr_model:'',api_key_configured:false},
      deepseek:{enabled:false,chat_model:'',api_key_configured:false}
    }}
  }, runtime:{active_environment:'test',database_engine:'SQLite',pending_restart:false,local_processing:true}
};
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('local presentation workflow', () => {
  it('can ask without credentials or external consent and preserves source attribution', async () => {
    const calls: {url:string; body?:string}[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({url, body:init?.body as string});
      const source = {id:'chunk-1',document_id:'law-1',title:'已发布资料',version:'现行版',article:'第三十三条',text:'已发布条款原文',source:'原文登记',allow_external:false};
      return {ok:true,json:async () => url.endsWith('/settings') ? settings : {status:'answered',answer:'已发布条款原文',claims:[{text:'已发布条款原文',source_ids:['chunk-1']}],sources:[source],provider:'local',model:'法规资料整理'}};
    }));
    render(<Assistant onKnowledge={vi.fn()} onSettings={vi.fn()}/>);
    await screen.findByText('知识服务');
    fireEvent.change(screen.getByLabelText('法规问题'), {target:{value:'居留证件变更'}});
    expect(screen.getByRole('button',{name:'生成回答'})).toBeEnabled();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'生成回答'}));
    await screen.findByRole('button',{name:'查看依据1'});
    const ask = calls.find(call => call.url.endsWith('/knowledge/ask'));
    expect(JSON.parse(ask!.body!).confirm_external).toBe(false);
    expect(screen.getByText('知识服务 · 法规资料整理')).toBeInTheDocument();
    expect(screen.queryByText(/演示模式|mock|模拟摘要|AI 未就绪/)).not.toBeInTheDocument();
  });

  it('keeps cloud consent and key gating in normal mode', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,json:async () => ({...settings,runtime:{...settings.runtime,local_processing:false}})})));
    render(<Assistant onKnowledge={vi.fn()} onSettings={vi.fn()}/>);
    fireEvent.click(screen.getByRole('button',{name:'AI 问答'}));
    await screen.findByText('请先配置 API 密钥，并启用 AI 和默认服务商。');
    fireEvent.change(screen.getByLabelText('法规问题'), {target:{value:'居留证件变更'}});
    expect(screen.getByRole('button',{name:'生成回答'})).toBeDisabled();
    expect(screen.getByRole('checkbox')).toBeInTheDocument();
  });
});


it('does not expose presentation controls in settings or contradict local service availability', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ok:true,json:async () => url.endsWith('/settings') ? settings : {counts:{},events:[]}})));
  render(<Settings/>);
  await screen.findByText('业务工作空间');
  expect(screen.queryByText(/演示|mock|模拟|业务调用已关闭/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', {name:/AI 服务/}));
  await screen.findByText('智能处理服务');
  expect(screen.queryByText('测试连接')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', {name:/数据与维护/}));
  expect(screen.queryByText(/演示|mock|模拟/)).not.toBeInTheDocument();
});
