import json
import httpx
import pytest
from pydantic import SecretStr
from backend.app import record_fun_asr as asr
from backend.app.records_ai import RecordAIError, provider_config
from backend.app.schemas import Settings, SettingsUpdate, persisted_settings, public_settings, merge_settings

@pytest.fixture
def cloud(monkeypatch,tmp_path):
    monkeypatch.setenv('CRJ_FUN_ASR_CACHE',str(tmp_path/'tasks.sqlite3'))
    state={'calls':[], 'status':'SUCCEEDED', 'url':'https://test.oss-cn-beijing.aliyuncs.com/result', 'http':200,
           'sentences':[{'text':'你叫什么名字？','speaker_id':0,'begin_time':0,'end_time':1000},
                        {'text':'我叫测试人员。','speaker_id':1,'begin_time':1100,'end_time':2500}]}
    original=httpx.Client
    def handler(req):
        state['calls'].append(req)
        if req.url.path.endswith('/uploads'):
            return httpx.Response(state['http'],json={'data':{'upload_host':'https://test.oss-cn-beijing.aliyuncs.com/',
                'upload_dir':'upload/test','max_file_size_mb':20,'oss_access_key_id':'temporary','signature':'signature','policy':'policy'}})
        if req.url.host=='test.oss-cn-beijing.aliyuncs.com':
            assert 'authorization' not in req.headers
            if req.method=='POST':return httpx.Response(200)
            return httpx.Response(200,json={'transcripts':[{'sentences':state['sentences']}]})
        if req.method=='POST':
            body=json.loads(req.content)
            assert body['model']=='fun-asr' and body['parameters']['diarization_enabled'] is True
            assert req.headers['X-DashScope-OssResourceResolve']=='enable'
            return httpx.Response(200,json={'output':{'task_id':'task-1'}})
        return httpx.Response(200,json={'output':{'task_status':state['status'],'results':[{'subtask_status':'SUCCEEDED','transcription_url':state['url']}]}})
    monkeypatch.setattr(asr.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    return state

def run():return asr.transcribe(b'fictional audio','mp3','audio/mpeg','secret-key')

def test_turns_and_resume(cloud):
    result=run()
    assert result['provider']=='aliyun'
    assert len(result['segments'])==2
    assert result['segments'][1]=={'speaker':'unknown','text':'[说话人 1] 我叫测试人员。','start_ms':1100,'end_ms':2500}
    cloud['calls'].clear();run()
    assert all(r.method=='GET' for r in cloud['calls'])
    assert not any(r.url.path.endswith('/uploads') for r in cloud['calls'])

@pytest.mark.parametrize('url',['http://test.oss-cn-beijing.aliyuncs.com/result','https://localhost/result','https://test.oss-cn-beijing.aliyuncs.com.evil.test/result'])
def test_reject_result_url(cloud,url):
    cloud['url']=url
    with pytest.raises(RecordAIError):run()
    assert all(str(r.url)!=url for r in cloud['calls'])

@pytest.mark.parametrize('status,expected',[(401,400),(403,400),(429,429),(500,502)])
def test_errors(cloud,status,expected):
    cloud['http']=status
    with pytest.raises(RecordAIError) as exc:run()
    assert exc.value.status==expected
    assert 'secret-key' not in str(exc.value)

@pytest.mark.parametrize('change',[{'text':None},{'end_time':-1},{'speaker_id':'bad space'}])
def test_invalid_turns(cloud,change):
    cloud['sentences'][0].update(change)
    with pytest.raises(RecordAIError):run()

def test_pending_resume(cloud,monkeypatch):
    cloud['status']='RUNNING';monkeypatch.setattr(asr,'MAX_WAIT',1)
    with pytest.raises(RecordAIError):run()
    cloud['status']='SUCCEEDED';cloud['calls'].clear()
    assert run()['segments']
    assert all(r.method=='GET' for r in cloud['calls'])

def test_config_secret_and_legacy():
    s=Settings();s.ai.speech.provider='aliyun';s.ai.speech.api_key=SecretStr('secret-key')
    assert 'secret-key' not in json.dumps(public_settings(s))
    assert persisted_settings(s)['ai']['speech']['api_key']=='secret-key'
    payload=persisted_settings(s);payload['ai'].pop('speech')
    assert merge_settings(SettingsUpdate.model_validate(payload),s).ai.speech.api_key.get_secret_value()=='secret-key'
    payload['ai']['speech']={'provider':'aliyun','api_key':''}
    assert merge_settings(SettingsUpdate.model_validate(payload),s).ai.speech.api_key.get_secret_value()=='secret-key'
    payload['ai']['speech']['clear_api_key']=True
    assert not merge_settings(SettingsUpdate.model_validate(payload),s).ai.speech.api_key.get_secret_value()

def test_no_silent_fallback():
    s=Settings();s.ai.enabled=True;s.ai.speech.provider='aliyun'
    with pytest.raises(RecordAIError):provider_config(s,'asr')
