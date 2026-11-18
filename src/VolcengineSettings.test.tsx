import { render } from './test/auth';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { Settings } from './pages/Settings';
import { defaultVolcengine } from './components/VolcengineSpeechSettings';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('saves independent Volcengine credentials and preserves them while switching providers', async () => {
  const state = {settings: {
    general: {system_name:'测试', organization:'测试组织'},
    environment:{mode:'test', postgres:{host:'localhost',port:5432,database:'nia',username:'nia',password_configured:false}},
    ai:{enabled:false, default_provider:'siliconflow',
      speech:{provider:'siliconflow', model:'fun-asr', workspace_id:'',api_key_configured:true,volcengine:{...defaultVolcengine}},
      providers:{siliconflow:{enabled:true,chat_model:'chat',asr_model:'Qwen/Qwen3-ASR-1.7B',api_key_configured:true},
        deepseek:{enabled:false,chat_model:'chat',api_key_configured:false}}}
  }, runtime:{active_environment:'test',database_engine:'SQLite',pending_restart:false}};
  const writes: Record<string, any>[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/ai-credentials')) return {ok:true,json:async()=>({siliconflow:'saved-sf',deepseek:''})};
    if (options?.method === 'PUT') {
      const body = JSON.parse(String(options.body)); writes.push(body);
      state.settings.ai.speech = {...body.ai.speech, api_key_configured:true,
        volcengine:{...defaultVolcengine,tos_bucket:body.ai.speech.volcengine.tos_bucket,api_key_configured:true,tos_access_key_configured:true,tos_secret_key_configured:true}};
    }
    return {ok:true,json:async()=>url.endsWith('/settings') ? structuredClone(state) : {items:[],total:0}};
  }));
  render(<Settings/>);
  await screen.findByText('数据库切换');
  fireEvent.click(screen.getByRole('tab', {name:/AI 服务/}));
  fireEvent.change(screen.getByLabelText('语音服务'), {target:{value:'volcengine'}});
  for (const [label, value] of [['火山语音 API Key','new-speech'], ['TOS Access Key ID','new-ak'],
    ['TOS Secret Access Key','new-sk'], ['TOS 私有桶名称','private-recordings']]) {
    fireEvent.change(screen.getByLabelText(label), {target:{value}});
  }
  fireEvent.change(screen.getByLabelText('语音服务'), {target:{value:'aliyun'}});
  expect(screen.getByLabelText('百炼 API Key')).toHaveValue('');
  fireEvent.change(screen.getByLabelText('语音服务'), {target:{value:'volcengine'}});
  expect(screen.getByLabelText('火山语音 API Key')).toHaveValue('new-speech');
  fireEvent.click(screen.getByRole('button', {name:/保存/}));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0].ai.speech.provider).toBe('volcengine');
  expect(writes[0].ai.speech.api_key).toBe('');
  expect(writes[0].ai.speech.volcengine).toMatchObject({api_key:'new-speech',tos_access_key:'new-ak',tos_secret_key:'new-sk',tos_bucket:'private-recordings'});
  expect(writes[0].ai.speech.volcengine).not.toHaveProperty('api_key_configured');
  await waitFor(() => expect(screen.getByLabelText('火山语音 API Key')).toHaveValue(''));
  fireEvent.click(screen.getByLabelText('清空火山语音 API Key'));
  fireEvent.click(screen.getByRole('button', {name:/保存/}));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[1].ai.speech.volcengine.clear_api_key).toBe(true);
  expect(writes[1].ai.speech.volcengine.clear_tos_secret_key).toBe(false);
});
