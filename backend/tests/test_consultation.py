import json
import pytest
from backend.tests.test_knowledge import client, enable_ai


def fake(monkeypatch, finish='stop'):
    calls = []
    def post(url, key, **kwargs):
        calls.append(kwargs['json'])
        return {'choices': [{'finish_reason': finish, 'message': {'content': json.dumps({'violation': '事实不足，尚不能认定违法。', 'recommendation': '请先明确签证与居留许可类型。', 'legal_basis': '需要核对官方原文。'})}}]}
    monkeypatch.setattr('backend.app.knowledge_ai.bounded_post', post)
    return calls


@pytest.mark.parametrize('endpoint', ['/ask', '/ask/stream'])
def test_consult_without_library_and_history(client, monkeypatch, endpoint):
    enable_ai(client)
    calls = fake(monkeypatch)
    response = client.post('/api/knowledge'+endpoint, json={'question':'签证咨询','mode':'consultation','confirm_external':True})
    assert response.status_code == 200
    if endpoint.endswith('stream'):
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        result = events[-1]['answer']
        history = client.get('/api/knowledge/conversations/'+events[0]['conversation_id']).json()
        assert history['items'][0]['mode'] == 'consultation'
        assert history['items'][0]['answer'] == result
    else:
        result = response.json()
    assert result['mode'] == 'consultation' and result['sources'] == []
    assert '未经法规原文核验' in result['answer']
    assert result['answer'].startswith('一、违法行为\n')
    assert '二、处理意见\n' in result['answer'] and '三、法律依据\n' in result['answer']
    assert len(calls) == 1
    assert set(json.loads(calls[0]['messages'][1]['content'])) == {'question','reference_date'}
    grounded = client.post('/api/knowledge/ask', json={'question':'签证咨询','confirm_external':True})
    assert grounded.json()['status'] == 'insufficient'
    assert len(calls) == 1


def test_consult_permissions_and_failures(client, monkeypatch):
    calls = fake(monkeypatch)
    body = {'question':'签证咨询','mode':'consultation','confirm_external':True}
    assert client.post('/api/knowledge/ask', json=body).status_code == 409
    enable_ai(client)
    assert client.post('/api/knowledge/ask', json={**body,'confirm_external':False}).status_code == 400
    assert client.post('/api/knowledge/ask', json={**body,'template_id':'x'}).status_code == 422
    assert calls == []
    fake(monkeypatch, 'length')
    assert client.post('/api/knowledge/ask', json=body).status_code == 502
    config = client.app.state.store.current.model_copy(deep=True)
    config.ai.external_permissions.question = False
    client.app.state.store.save(config)
    assert client.post('/api/knowledge/ask/stream', json=body).status_code == 403


@pytest.mark.parametrize('endpoint', ['/ask', '/ask/stream'])
def test_auto_routes_by_available_sources(client, monkeypatch, endpoint):
    from backend.tests.test_knowledge import upload, publish
    enable_ai(client)
    calls = fake(monkeypatch)
    body = {'question':'签证延期','mode':'auto','confirm_external':True}
    def result():
        response = client.post('/api/knowledge'+endpoint, json=body)
        assert response.status_code == 200
        if endpoint.endswith('stream'):
            return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')][-1]['answer']
        return response.json()
    assert result()['mode'] == 'consultation'
    document = upload(client).json()
    publish(client, document, allow_external=False)
    assert result()['mode'] == 'consultation'
    publish(client, document, allow_external=True)
    grounded_calls = []
    def grounded(question, sources, settings):
        grounded_calls.append(sources)
        return {'status':'answered','answer':'有引用的分析','sources':sources,'claims':[], 'provider':'siliconflow','model':'test'}
    def stream(*args):
        yield {'type':'complete','answer':grounded(*args)}
    monkeypatch.setattr('backend.app.knowledge.answer_with_sources',grounded)
    monkeypatch.setattr('backend.app.knowledge.stream_answer',stream)
    answer = result()
    assert answer['mode'] == 'grounded' and answer['sources']
    assert len(calls) == 2 and len(grounded_calls) == 1
