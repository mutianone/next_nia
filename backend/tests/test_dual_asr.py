from pydantic import SecretStr
from threading import Barrier
import pytest
from backend.app import record_dual_asr as dual, records_ai, records, record_voice_audio, record_media
from backend.app.schemas import Settings, VolcengineSettings, public_settings
from backend.tests.test_records import create, upload, enable_ai, editable
from backend.tests.test_record_capture import client
from backend.app import record_jobs


def settings():
    s = Settings()
    s.ai.enabled = True
    s.ai.providers.siliconflow.enabled = True
    s.ai.providers.siliconflow.api_key = SecretStr('fake-sf-secret')
    s.ai.speech.provider = 'dual'
    s.ai.speech.volcengine = VolcengineSettings(api_key='fake-volc-secret', tos_access_key='fake-ak', tos_secret_key='fake-sk', tos_bucket='test-bucket')
    return s


def output(texts, engine):
    return {'provider': engine, 'model': 'fixture', 'text': ''.join(texts), 'raw_result': {'text': ''.join(texts)},
            'segments': [{'text': t, 'speaker': 'unknown', 'speaker_id': str(i != 0), 'channel_id': 0,
                          'recognition_run_id': engine, 'start_ms': i*2000, 'end_ms': i*2000+1000,
                          'timestamp_source': 'PROVIDER'} for i,t in enumerate(texts)]}


def pair():
    return {'siliconflow': output(['你确定吗？', '不确定。', '还有信息。'], 'siliconflow'),
            'volcengine': output(['你确定吗？', '确定。', '还有信息。'], 'volcengine')}


def test_parallel_same_audio_fixed_qwen_and_independent_config(monkeypatch):
    s = settings(); original_model = s.ai.providers.siliconflow.asr_model
    barrier = Barrier(2); calls = []
    def transcribe(raw, fmt, mime, config, **kwargs):
        calls.append((config.ai.speech.provider, config.ai.providers.siliconflow.asr_model, kwargs))
        barrier.wait(timeout=5)
        return pair()[config.ai.speech.provider]
    monkeypatch.setattr(records_ai, 'transcribe_audio', transcribe)
    monkeypatch.setattr(record_media, 'normalize_for_asr', lambda *a, **kw: (b'normalized', 'mp3', 'audio/mpeg'))
    monkeypatch.setattr(record_voice_audio, 'available', lambda: False)
    result = dual.transcribe(b'audio', 'wav', 'audio/wav', s, speaker_count=2, request_key='request')
    assert len(calls) == 2 and all(c[2]['_prepared'][0] == b'normalized' for c in calls)
    assert next(c for c in calls if c[0]=='siliconflow')[1] == dual.QWEN
    assert next(c for c in calls if c[0]=='siliconflow')[2]['speaker_count'] is None
    assert s.ai.providers.siliconflow.asr_model == original_model and s.ai.speech.provider == 'dual'
    assert result['segments'][1]['dual_review']['status'] == 'conflict'
    assert all(p['speaker_id'] is None and p['voice_review'] for p in result['segments'])
    assert set(result['raw_result']['engines']) == set(dual.ENGINES)


def test_preflight_missing_key_does_not_send(monkeypatch):
    s = settings(); s.ai.providers.siliconflow.api_key = SecretStr('')
    monkeypatch.setattr(records_ai, 'transcribe_audio', lambda *a, **kw: pytest.fail('must not send'))
    with pytest.raises(records_ai.RecordAIError):
        dual.transcribe(b'a', 'mp3', 'audio/mpeg', s)
    assert not dual.ready(s)


def test_partial_failure_is_not_agreement_and_exception_is_redacted(monkeypatch):
    monkeypatch.setattr(record_media, 'normalize_for_asr', lambda *a, **kw: (b'a', 'mp3', 'audio/mpeg'))
    monkeypatch.setattr(record_voice_audio, 'available', lambda: False)
    def transcribe(raw, fmt, mime, config, **kw):
        if config.ai.speech.provider == 'volcengine':
            raise RuntimeError('secret-signed-url')
        return pair()['siliconflow']
    monkeypatch.setattr(records_ai, 'transcribe_audio', transcribe)
    result = dual.transcribe(b'a', 'mp3', 'audio/mpeg', settings())
    assert all(p['dual_review']['status'] == 'single' for p in result['segments'])
    assert 'secret-signed-url' not in str(result)
    assert result['raw_result']['errors']['volcengine']['status'] == 502
    with pytest.raises(records_ai.RecordAIError):
        dual.combine({})


def test_one_to_many_missing_timing_and_supplements():
    data = pair(); data['volcengine']['segments'] = [{'text':'你确定吗？确定。还有信息。','start_ms':0,'end_ms':5000,'speaker_id':'0'}]
    result = dual.combine(data)
    assert len(result['segments']) == 1
    assert len(result['segments'][0]['dual_review']['candidates'][0]['segments']) == 3
    assert data['siliconflow']['segments'][0].get('dual_review') is None
    data['siliconflow']['segments'][0]['start_ms'] = None
    result = dual.combine(data)
    assert result['segments'][0]['start_ms'] is None
    assert result['segments'][0]['dual_review']['status'] == 'untimed'
    del data['volcengine']['segments']
    result = dual.combine(data)
    assert '还有信息' in result['segments'][0]['text']


def transcribed(client, monkeypatch, data=None, duration=6000):
    enable_ai(client)
    client.app.state.store.current = settings()
    row = upload(client, create(client), duration=duration).json()
    monkeypatch.setattr(records, 'transcribe_audio', lambda *a, **kw: dual.combine(data or pair()))
    response = client.post(f"/api/records/{row['id']}/audio/{row['audio_clips'][0]['id']}/transcribe",
                           json={'expected_revision':row['revision'], 'confirmed':True, 'speaker_count':2})
    assert response.status_code == 200, response.text
    return response.json()


def test_api_review_preserves_originals_scopes_voices_and_blocks_unreviewed_fill(client, monkeypatch):
    row = transcribed(client, monkeypatch); root = f"/api/records/{row['id']}"
    material = client.get(root+'/dual-review').json()
    run = material['runs'][0]
    for path in ('/analyze', '/generation-jobs'):
        resp = client.post(root+path, json={'expected_revision':row['revision'], 'confirmed':True})
        assert resp.status_code == 409, resp.text
    resp = client.post(root+'/qa-drafts/rebuild', json={'expected_revision':row['revision']})
    assert resp.status_code == 409
    body = {'expected_revision': row['revision'], 'confirmed': True, 'choices':['volcengine','siliconflow','siliconflow']}
    resp = client.post(root+'/dual-review/'+run['id'], json=body)
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert [s['text'] for s in updated['segments']] == ['你确定吗？','不确定。','还有信息。']
    assert updated['segments'][0]['speaker_id'] != updated['segments'][1]['speaker_id']
    assert updated['segments'][1]['speaker_id'] == updated['segments'][2]['speaker_id']
    assert all(s['speaker'] == 'unknown' for s in updated['segments'])
    runs = client.get(root+'/recognition-runs').json()['items']
    assert len(runs) == 2 and sum(r['applied'] for r in runs) == 1
    assert next(r for r in runs if r['provider']=='dual')['sentences'][1]['text'] == '不确定。'
    assert client.post(root+'/dual-review/'+run['id'], json=body).status_code == 409
    resp = client.post(root+'/qa-drafts/rebuild', json={'expected_revision': updated['revision']})
    assert resp.status_code == 200, resp.text


def test_manual_role_edits_do_not_bypass_gate_or_get_overwritten(client, monkeypatch):
    row = transcribed(client, monkeypatch); root = f"/api/records/{row['id']}"
    run_id = client.get(root+'/dual-review').json()['runs'][0]['id']
    updated = client.patch(root+'/segments', json={'expected_revision':row['revision'], 'segments':[editable(s,speaker='interviewee') for s in row['segments']]}).json()
    body = {'expected_revision':updated['revision'], 'confirmed':True}
    assert client.post(root+'/analyze', json=body).status_code == 409
    assert client.post(root+'/dual-review/'+run_id, json={**body,'choices':['siliconflow']*3}).status_code == 409


def test_missing_candidate_cannot_drop_source_and_secret_settings_hidden(client, monkeypatch):
    data = pair(); data['volcengine']['segments'].pop()
    row = transcribed(client, monkeypatch, data); root = f"/api/records/{row['id']}"
    run_id = client.get(root+'/dual-review').json()['runs'][0]['id']
    assert client.post(root+'/dual-review/'+run_id, json={'expected_revision':row['revision'],'confirmed':True,'choices':['siliconflow']}).status_code == 422
    assert client.post(root+'/dual-review/'+run_id, json={'expected_revision':row['revision'],'confirmed':True,'choices':['volcengine']*3}).status_code == 422
    public = public_settings(settings())
    assert public['ai']['speech']['provider'] == 'dual'
    assert 'fake-' not in str(public)


def test_out_of_audio_timestamp_retains_original_without_fabricating_clip_time(client, monkeypatch):
    data = pair(); data['volcengine']['segments'][-1]['end_ms'] = 6030
    row = transcribed(client, monkeypatch, data); root = f"/api/records/{row['id']}"
    run_id = client.get(root+'/dual-review').json()['runs'][0]['id']
    response = client.post(root+'/dual-review/'+run_id, json={'expected_revision':row['revision'],'confirmed':True,'choices':['volcengine']*3})
    assert response.status_code == 200, response.text
    last = response.json()['segments'][-1]
    assert last['start_ms'] is None and last['end_ms'] is None and last['voice_review']
    assert last['dual_source']['original']['end_ms'] == 6030


def test_pending_gate_survives_deleting_projection_and_other_record_isolation(client, monkeypatch):
    row = transcribed(client, monkeypatch); root = f"/api/records/{row['id']}"
    run_id = client.get(root+'/dual-review').json()['runs'][0]['id']
    other = create(client)
    assert client.post(f"/api/records/{other['id']}/dual-review/{run_id}", json={'expected_revision':other['revision'],'confirmed':True,'choices':['siliconflow']*3}).status_code == 404
    updated = client.patch(root+'/segments', json={'expected_revision':row['revision'],'segments':[]}).json()
    assert client.post(root+'/generation-jobs', json={'expected_revision':updated['revision'],'confirmed':True}).status_code == 409
    response = client.post(root+'/dual-review/'+run_id, json={'expected_revision':updated['revision'],'confirmed':True,'choices':['siliconflow']*3})
    assert response.status_code == 200 and len(response.json()['segments']) == 3


@pytest.mark.parametrize('review', [True, False])
def test_mixed_conflict_prefills_independent_template_answers(client, monkeypatch, review):
    enable_ai(client)
    client.app.state.store.current = settings()
    row = upload(client, create(client, scenario='marriage', template_id='bride'), duration=30000).json()
    questions = {f['id']: f['label'] for f in row['template_snapshot']['fields']}
    engines = {}
    for provider, disputed in [('siliconflow', '是的。'), ('volcengine', '不是。')]:
        texts = [questions['q004'], '我有两个孩子。', '还有信息。', questions['q005'], disputed]
        engines[provider] = output(texts, provider)
        for part, voice in zip(engines[provider]['segments'], ['甲', '乙', '乙', '甲', '乙']):
            part['speaker_id'] = voice
    monkeypatch.setattr(records, 'transcribe_audio', lambda *a, **kw: dual.combine(engines))
    root = f"/api/records/{row['id']}"
    result = client.post(root + f"/audio/{row['audio_clips'][0]['id']}/transcribe",
                         json={'expected_revision': row['revision'], 'confirmed': True, 'speaker_count': 2})
    assert result.status_code == 200, result.text
    saved = client.get(root).json()
    template = saved['template_snapshot']
    assert template['dual_prefill'] == {'q004': '我有两个孩子。\n还有信息。'}
    assert template['values']['q004'] == template['dual_prefill']['q004']
    assert not template['values'].get('q005')
    assert template['sources']['q004'] == [s['id'] for s in saved['segments'][:3]]
    run = client.get(root + '/dual-review').json()['runs'][0]
    url = root + '/dual-review/' + run['id']
    again = client.post(url + '/prefill', json={'expected_revision': saved['revision']})
    assert again.status_code == 200 and again.json()['revision'] == saved['revision']
    if review:
        reviewed = client.post(url, json={'expected_revision': saved['revision'], 'confirmed': True,
                                          'choices': ['volcengine'] * len(run['groups'])})
        assert reviewed.status_code == 200, reviewed.text
        saved = reviewed.json()
        assert saved['template_snapshot']['values']['q004'] == template['dual_prefill']['q004']
        assert saved['template_snapshot']['sources']['q004'] == [s['id'] for s in saved['segments'][:3]]
    # Editing the evidence invalidates the provisional answer.
    segments = [editable(s) for s in saved['segments']]
    segments[1]['text'] = '我有三个孩子。'
    edited = client.patch(root + '/segments', json={'expected_revision': saved['revision'], 'segments': segments})
    assert edited.status_code == 200, edited.text
    assert not edited.json()['template_snapshot']['values'].get('q004')
