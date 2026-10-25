"""Sample workspaces must use the same real provider adapters as every workspace."""
import json
import httpx
import pytest
from backend.tests.auth_client import TestClient
from backend.tests.test_knowledge import upload, publish, enable_ai
from backend.app.main import create_app
from backend.app import records_ai


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('NEXT_NIA_PRESENTATION', '1')
    with TestClient(create_app(tmp_path)) as instance:
        yield instance


def transport(monkeypatch, handler):
    real = httpx.Client
    calls = []
    def handle(request):
        calls.append(request)
        return handler(request)
    monkeypatch.setattr(records_ai.httpx, 'Client', lambda **kwargs: real(transport=httpx.MockTransport(handle), **kwargs))
    return calls


def test_sample_workspace_never_bypasses_keys_or_confirmation(client, monkeypatch):
    calls = transport(monkeypatch, lambda _: pytest.fail('unconfigured request contacted provider'))
    assert client.get('/api/settings').json()['runtime']['sample_workspace'] is True
    capabilities = client.get('/api/records/capabilities').json()['ai']
    assert not capabilities['asr']['ready'] and capabilities['asr']['provider'] == 'siliconflow'
    row = client.post('/api/records', json={'title':'接口核对', 'scenario':'residence'}).json()
    base = '/api/records/' + row['id']
    row = client.post(base+'/audio-library', json={'expected_revision':row['revision']}).json()
    path = base+'/audio/'+row['audio_clips'][0]['id']+'/transcribe'
    assert client.post(path, json={'expected_revision':row['revision']}).status_code == 400
    assert client.post(path, json={'expected_revision':row['revision'], 'confirmed':True}).status_code == 409
    assert client.get(base).json()['segments'] == []
    assert calls == []


def test_sample_workspace_uses_provider_for_all_ai_and_keeps_failed_attempt(client, monkeypatch):
    enable_ai(client)
    failure = False
    def response(request):
        if failure:
            return httpx.Response(503)
        if request.url.path.endswith('/transcriptions'):
            assert b'filename="recording.mp3"' in request.content
            return httpx.Response(200,json={'text':'今天下午三点召开工作会议。'})
        payload = json.loads(request.content)
        inputs = json.loads(payload['messages'][1]['content'])
        if isinstance(inputs, list):
            output = {'summary':'会议在下午三点举行。','key_facts':[{'label':'会议时间','value':'下午三点','segment_ids':[inputs[0]['segment_id']]}],'issues':[]}
        else:
            output = {'claims':[{'text':'签证延期需要提交证明材料。','source_ids':[inputs['evidence'][0]['source_id']]}]}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(output)}}]})
    calls = transport(monkeypatch,response)
    people = client.get('/api/people').json()['items']
    person_id = people[0]['id']
    row = client.post('/api/records',json={'title':'实际接口链路','scenario':'residence','person_id':person_id}).json()
    base = '/api/records/'+row['id']
    # A real small MP3 asset travels through upload, provider transport and storage.
    from pathlib import Path
    raw = (Path(__file__).resolve().parents[1]/'assets'/'healthcheck.mp3').read_bytes()
    row = client.post(base+'/audio',data={'expected_revision':str(row['revision'])},files={'file':('private-name.mp3',raw,'audio/mpeg')}).json()
    path = base+'/audio/'+row['audio_clips'][0]['id']+'/transcribe'
    response = client.post(path,json={'expected_revision':row['revision'],'confirmed':True})
    assert response.status_code == 200, response.text
    row=response.json()
    assert row['segments'][0]['text']=='今天下午三点召开工作会议。'
    assert row['transcription_attempts'][0]['provider']=='siliconflow'
    row=client.post(base+'/analyze',json={'expected_revision':row['revision'],'confirmed':True}).json()
    assert row['analysis']['provider']=='siliconflow' and not row['analysis']['stale']
    document=upload(client).json()
    assert publish(client,document,allow_external=True).status_code==200
    response=client.post('/api/knowledge/ask',json={'question':'签证延期需要什么材料','confirm_external':True})
    assert response.status_code==200, response.text
    assert response.json()['provider']=='siliconflow'
    assert len(calls)==3
    assert client.get(base+'/export.docx?include_ai=true').status_code==200
    candidates = client.get('/api/profiles/'+person_id+'/analysis-facts').json()['items']
    assert len(candidates)==1 and candidates[0]['sources'][0]['text']=='今天下午三点召开工作会议。'
    registration={'analysis_id':row['analysis']['id'],'fact_index':0}
    registered=client.post('/api/profiles/'+person_id+'/facts/from-analysis',json=registration)
    assert registered.status_code==201,registered.text
    fact=registered.json()
    assert not fact['verified'] and fact['source_snapshot']['provider']=='siliconflow'
    assert client.post('/api/profiles/'+person_id+'/facts/from-analysis',json=registration).json()['id']==fact['id']
    assert client.get('/api/profiles/'+person_id+'/analysis-facts').json()['items'][0]['imported']
    edited=client.put('/api/profiles/'+person_id+'/facts/'+fact['id'],json={'expected_revision':fact['revision'],'kind':'other','label':fact['label'],'value':fact['value'],'source_type':'manual','source':'核对原音后的人工登记','verified':True,'review_note':'人工核对原音和时间。'})
    assert edited.status_code==200,edited.text
    assert edited.json()['source_snapshot']['analysis_origin']['segments'][0]['text']=='今天下午三点召开工作会议。'
    assert client.post('/api/profiles/'+person_id+'/facts/from-analysis',json=registration).json()['id']==fact['id']
    assert client.post('/api/profiles/'+people[1]['id']+'/facts/from-analysis',json=registration).status_code==404
    failure=True
    response=client.post(base+'/analyze',json={'expected_revision':row['revision'],'confirmed':True})
    assert response.status_code==502
    updated=client.get(base).json()
    assert any(a['status']=='failed' for a in updated['analyses'])
    assert updated['analysis']['id']==row['analysis']['id']
    changed=client.patch(base+'/segments',json={'expected_revision':row['revision'],'segments':[{'id':row['segments'][0]['id'],'text':'时间改为下午四点。','speaker':'unknown'}]})
    assert changed.status_code==200,changed.text
    assert client.get('/api/profiles/'+person_id+'/analysis-facts').json()['items']==[]
    assert client.post('/api/profiles/'+person_id+'/facts/from-analysis',json=registration).status_code==409


def test_unapproved_seed_laws_cannot_be_sent(client,monkeypatch):
    calls=transport(monkeypatch,lambda _:pytest.fail('private source sent'))
    enable_ai(client)
    result=client.post('/api/knowledge/ask',json={'question':'申请延长居留期限，应当何时提出申请？','confirm_external':True}).json()
    assert result['status']=='insufficient' and calls==[]


def test_restart_preserves_work_without_duplicate_seed(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXT_NIA_PRESENTATION','1')
    with TestClient(create_app(tmp_path)) as client:
        count=client.get('/api/dashboard').json()['stats']['people']
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/dashboard').json()['stats']['people']==count


def test_existing_business_directory_is_never_seeded(tmp_path,monkeypatch):
    monkeypatch.delenv('NEXT_NIA_PRESENTATION',raising=False)
    with TestClient(create_app(tmp_path)):
        pass
    original=(tmp_path/'test.sqlite3').read_bytes()
    monkeypatch.setenv('NEXT_NIA_PRESENTATION','1')
    from backend.app.config import ConfigurationError
    with pytest.raises(ConfigurationError,match='独立'):
        with TestClient(create_app(tmp_path)):
            pass
    assert (tmp_path/'test.sqlite3').read_bytes()==original


@pytest.mark.parametrize('provider,capability',[('siliconflow','chat'),('siliconflow','asr'),('deepseek','chat')])
def test_actual_capability_checks_are_enabled_in_sample_workspace(client,monkeypatch,provider,capability):
    def response(request):
        assert request.method=='POST'
        if capability=='asr':
            return httpx.Response(200,json={'text':'这是语音测试，今天下午三点召开会议。'})
        payload=json.loads(request.content)
        if provider=='deepseek':
            assert request.url.host=='api.deepseek.com' and payload['thinking']=={'type':'disabled'}
        else:
            assert payload['enable_thinking'] is False
        inputs=json.loads(payload['messages'][1]['content'])
        output={'summary':'下午三点开会。','key_facts':[{'label':'地点','value':'二楼会议室','segment_ids':[inputs[0]['segment_id']]}],'issues':[]}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(output)}}]})
    calls=transport(monkeypatch,response)
    result=client.post('/api/settings/test-ai',json={'provider':provider,'capability':capability,'api_key':'unit-test-key'})
    assert result.status_code==200, result.text
    assert result.json()['capability']==capability and len(calls)==1
    assert not client.get('/api/settings').json()['settings']['ai']['enabled']


@pytest.mark.parametrize('status',[401,402,429,503])
def test_real_check_reports_upstream_failure_and_never_saves_key(client,monkeypatch,status):
    calls=transport(monkeypatch,lambda _:httpx.Response(status,json={'error':'unit-test-key'}))
    response=client.post('/api/settings/test-ai',json={'provider':'deepseek','capability':'chat','api_key':'unit-test-key'})
    assert response.status_code==({401:400,402:402,429:429,503:502}[status])
    assert 'unit-test-key' not in response.text and len(calls)==1
    assert not client.get('/api/settings').json()['settings']['ai']['providers']['deepseek']['api_key_configured']


def test_unintelligible_asr_diagnostic_is_not_success(client,monkeypatch):
    transport(monkeypatch,lambda _:httpx.Response(200,json={'text':'无关文字'}))
    response=client.post('/api/settings/test-ai',json={'provider':'siliconflow','capability':'asr','api_key':'unit-test-key'})
    assert response.status_code==502


def test_populated_workspace_can_probe_production_database(client,monkeypatch):
    monkeypatch.setattr('backend.app.main.probe_postgres',lambda _:5)
    response=client.post('/api/settings/test-database',json={'host':'127.0.0.1','port':5432,'database':'nia','username':'nia'})
    assert response.status_code==200
