import json
from sqlalchemy.orm import Session
from backend.tests.test_knowledge import client, enable_ai
from backend.app.assistant_history import AssistantConversation, AssistantMessage, recover_history

PATH='/api/knowledge/conversations'
def send(client, **extra):
    response=client.post('/api/knowledge/ask/stream',json={'question':'核查居留事项','template_id':'builtin-illegal-stay','template_revision':1,'confirm_external':True,**extra})
    return response,[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
def fake_answer(monkeypatch):
    def stream(question,sources,settings,**kwargs):
        yield {'type':'complete','answer':{'status':'answered','answer':'有引用的结论','claims':[],'sources':sources,'provider':'siliconflow','model':'test'}}
    monkeypatch.setattr('backend.app.knowledge.stream_answer',stream)

def test_history_saved_before_complete_and_reloads(client,monkeypatch):
    enable_ai(client);fake_answer(monkeypatch)
    response,events=send(client)
    assert response.status_code==200
    ids=events[0];assert ids['type']=='saved'
    history=client.get(PATH).json()['items'];assert len(history)==1
    detail=client.get(PATH+'/'+ids['conversation_id']).json()
    assert detail['items'][0]['answer']['answer']=='有引用的结论'
    assert detail['items'][0]['answer']['sources'][0]['text']
    assert detail['items'][0]['templateRevision']==1
    send(client,conversation_id=ids['conversation_id'])
    assert len(client.get(PATH+'/'+ids['conversation_id']).json()['items'])==2

def test_other_account_cannot_read_or_append(client,monkeypatch):
    enable_ai(client);fake_answer(monkeypatch)
    _,events=send(client);cid=events[0]['conversation_id']
    with Session(client.app.state.engine) as session,session.begin():
        session.get(AssistantConversation,cid).owner_id='another-account'
    assert client.get(PATH).json()['items']==[]
    assert client.get(PATH+'/'+cid).status_code==404
    assert send(client,conversation_id=cid)[0].status_code==404

def test_error_and_restart_persist_without_partial_output(client,monkeypatch):
    from backend.app.knowledge_ai import KnowledgeAIError
    enable_ai(client)
    def fail(*args,**kwargs):
        raise KnowledgeAIError(502,'服务暂不可用')
        yield
    monkeypatch.setattr('backend.app.knowledge.stream_answer',fail)
    _,events=send(client);cid=events[0]['conversation_id']
    message=client.get(PATH+'/'+cid).json()['items'][0]
    assert message['status']=='error' and message['error']=='服务暂不可用'
    assert 'answer' not in message
    with Session(client.app.state.engine) as session,session.begin():
        session.get(AssistantMessage,message['id']).status='pending'
    recover_history(client.app.state.engine)
    assert '服务已重启' in client.get(PATH+'/'+cid).json()['items'][0]['error']
