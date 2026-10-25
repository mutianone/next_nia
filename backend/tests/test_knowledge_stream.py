import json
import httpx
import pytest
from backend.app.schemas import Settings
from backend.app.knowledge_stream import stream_answer
from backend.app.knowledge_ai import KnowledgeAIError
from backend.tests.test_knowledge import client, upload, publish, enable_ai

SOURCES = [{'id':'source-1','title':'公开规定','version':'1','article':'第一条','text':'申请应提交材料。','effective_from':'2025-01-01','effective_to':None,'allow_external':True}]

class Packets(httpx.SyncByteStream):
    def __init__(self, packets): self.packets = packets
    def __iter__(self):
        for packet in self.packets:
            encoded = packet.encode()
            for i in range(0,len(encoded),7): yield encoded[i:i+7]

def wire(text, finish='stop', done=True):
    chunks = [text[:len(text)//2], text[len(text)//2:]]
    packets = ['data: '+json.dumps({'choices':[{'delta':{'content':part},'finish_reason':None}]},ensure_ascii=False)+'\n\n' for part in chunks]
    packets += ['data: '+json.dumps({'choices':[{'delta':{},'finish_reason':finish}]})+'\n\n']
    return packets + (['data: [DONE]\n\n'] if done else [])

def setup(monkeypatch, packets, provider='siliconflow'):
    settings=Settings(); settings.ai.enabled=True; settings.ai.default_provider=provider
    from pydantic import SecretStr
    configured=getattr(settings.ai.providers,provider); configured.enabled=True; configured.api_key=SecretStr('stream-test-secret')
    real=httpx.Client
    def handle(request):
        assert json.loads(request.content)['stream'] is True
        return httpx.Response(200,stream=Packets(packets))
    monkeypatch.setattr(httpx,'Client',lambda **kwargs: real(transport=httpx.MockTransport(handle),**kwargs))
    return settings

@pytest.mark.parametrize('provider',['siliconflow','deepseek'])
def test_real_upstream_deltas_checked_then_completed(monkeypatch,provider):
    raw=json.dumps({'claims':[{'text':'应提交材料。','source_ids':['source-1']}]},ensure_ascii=False)
    settings=setup(monkeypatch,wire(raw),provider)
    events=list(stream_answer('材料要求',SOURCES,settings))
    assert [e['type'] for e in events]==['partial','complete']
    assert events[-1]['answer']['answer']=='应提交材料。 [1]'

@pytest.mark.parametrize('raw,finish,done',[
    ('{"claims":[{"text":"结论","source_ids":["fabricated"]}]}','stop',True),
    ('{"claims":[]}',None,False),
    ('{"claims":[]}','length',True),
    ('{"claims":[{"text":"stream-test-secret","source_ids":["source-1"]}]}','stop',True),
    ('{"claims":[] BROKEN','stop',True),
])
def test_incomplete_or_unverifiable_output_rejected(monkeypatch,raw,finish,done):
    settings=setup(monkeypatch,wire(raw,finish,done))
    with pytest.raises(KnowledgeAIError): list(stream_answer('问题',SOURCES,settings))

def test_stream_endpoint_permissions_and_final_event(client,monkeypatch):
    document=upload(client).json(); publish(client,document); enable_ai(client)
    calls=[]
    def fake(question,sources,settings):
        calls.append(question)
        yield {'type':'complete','answer':{'status':'answered','answer':'结论','claims':[],'sources':sources,'provider':'siliconflow','model':'test'}}
    monkeypatch.setattr('backend.app.knowledge.stream_answer',fake)
    response=client.post('/api/knowledge/ask/stream',json={'question':'签证延期','confirm_external':True})
    assert response.status_code==200
    assert 'text/event-stream' in response.headers['content-type']
    events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert events[0]['type']=='saved' and events[-1]['type']=='complete'
    settings=client.app.state.store.load(); settings.ai.external_permissions.question=False; client.app.state.store.save(settings)
    assert client.post('/api/knowledge/ask/stream',json={'question':'签证延期','confirm_external':True}).status_code==403
    assert len(calls)==1

def test_more_than_twelve_claims_stream_and_validate(monkeypatch):
    claims=[{'text':f'第{i+1}项核查结论','source_ids':['source-1']} for i in range(24)]
    settings=setup(monkeypatch,wire(json.dumps({'claims':claims},ensure_ascii=False)))
    events=list(stream_answer('逐项核查',SOURCES,settings))
    assert events[-1]['type']=='complete'
    assert len(events[-1]['answer']['claims'])==24

def test_claim_limit_remains_bounded():
    from backend.app.knowledge_stream import complete_claims
    from backend.app.knowledge_ai import MAX_CLAIMS
    raw=json.dumps({'claims':[{'text':'结论','source_ids':['source-1']} for _ in range(MAX_CLAIMS+1)]})
    with pytest.raises(KnowledgeAIError):complete_claims(raw)
