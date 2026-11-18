"""Protocol fixtures only: these tests do not claim live recognition quality."""
from copy import deepcopy
import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from backend.app import record_volcengine as asr, records_ai
from backend.app.schemas import Settings, SettingsUpdate, VolcengineSettings, public_settings, persisted_settings, merge_settings
from backend.tests.test_records import client, create, upload


def config():
    return VolcengineSettings(api_key='speech-test-secret', tos_access_key='tos-test-access',
        tos_secret_key='tos-test-secret', tos_bucket='test-bucket')


def payload():
    return {'audio_info': {'duration': 8000}, 'result': {'text': '你还有补充吗？有。还有信息。', 'utterances': [
        {'text': text, 'start_time': begin, 'end_time': end, 'additions': {'speaker': speaker, 'channel_id': '1'},
         'words': [{'text': text, 'start_time': begin, 'end_time': end}]}
        for text, begin, end, speaker in [('你还有补充吗？', 0, 1800, '0'), ('有。', 2000, 2500, '1'), ('还有信息。', 4000, 5500, '1')]]}}


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    monkeypatch.setenv('CRJ_VOLC_ASR_CACHE', str(tmp_path / 'tasks.sqlite3'))
    state = {'calls': [], 'code': '20000000', 'payload': payload(), 'http': 200, 'submit_code': '20000000'}
    original = httpx.Client
    def handler(req):
        state['calls'].append(req)
        if req.url.host == 'test-bucket.tos-cn-beijing.volces.com':
            assert 'x-api-key' not in req.headers
            assert 'X-Tos-Signature' in req.url.params
            if req.method == 'PUT':
                assert req.headers['x-tos-acl'] == 'private'
                assert req.content == b'fixture audio'
            return httpx.Response(200)
        assert req.url.host == 'openspeech.bytedance.com'
        assert req.headers['X-Api-Key'] == 'speech-test-secret'
        assert req.headers['X-Api-Resource-Id'] == 'volc.seedasr.auc'
        if req.url.path.endswith('/submit'):
            body = json.loads(req.content)
            assert body['request']['enable_speaker_info'] is True
            assert body['request']['enable_ddc'] is False
            assert body['request']['show_utterances'] is True
            return httpx.Response(state['http'], headers={'X-Api-Status-Code': state['submit_code']})
        assert json.loads(req.content) == {}
        return httpx.Response(200, headers={'X-Api-Status-Code': state['code']}, json=state['payload'])
    monkeypatch.setattr(asr.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    return state


def run(count=2, request_key='clip-test'):
    return asr.transcribe(b'fixture audio', 'mp3', 'audio/mpeg', config(), request_key=request_key, speaker_count=count)


def test_upload_native_short_reply_supplement_and_resume(cloud):
    result = run()
    assert result['provider'] == 'volcengine' and result['model'] == 'volc.seedasr.auc'
    assert [s['text'] for s in result['segments']] == ['你还有补充吗？', '有。', '还有信息。']
    assert [s['speaker_id'] for s in result['segments']] == ['0', '1', '1']
    assert all(s['speaker'] == 'unknown' and s['timestamp_source'] == 'PROVIDER' for s in result['segments'])
    assert result['segments'][2]['words'][0]['begin_time'] == 4000
    assert result['raw_result'] == cloud['payload']
    assert [r.method for r in cloud['calls']] == ['PUT', 'POST', 'POST', 'DELETE']
    submitted = json.loads(cloud['calls'][1].content)
    assert submitted['request']['ssd_version'] == '200'
    assert 'speaker_count' not in submitted['request']  # no undocumented exact-count promise
    cloud['calls'].clear()
    assert run()['recognition_run_id'] == result['recognition_run_id']
    assert [r.url.path for r in cloud['calls'] if r.method == 'POST'] == ['/api/v3/auc/bigmodel/query']


def test_pending_task_reuses_upload_and_id(cloud, monkeypatch):
    cloud['code'] = '20000002'
    monkeypatch.setattr(asr, 'MAX_WAIT', 1)
    with pytest.raises(records_ai.RecordAIError) as error:
        run()
    assert error.value.status == 504
    assert not any(r.method == 'DELETE' for r in cloud['calls'])
    task_id = cloud['calls'][1].headers['X-Api-Request-Id']
    cloud['calls'].clear(); cloud['code'] = '20000000'
    run()
    assert cloud['calls'][0].headers['X-Api-Request-Id'] == task_id
    assert not any(r.method == 'PUT' for r in cloud['calls'])


def test_cache_separates_count_and_clip(cloud):
    for count, clip in [(2, 'a'), (2, 'a'), (6, 'a'), (2, 'b')]:
        run(count, clip)
    bodies = [json.loads(r.content) for r in cloud['calls'] if r.url.path.endswith('/submit')]
    assert [b['request']['ssd_version'] for b in bodies] == ['200', '300', '200']


@pytest.mark.parametrize('http,expected', [(401, 400), (403, 400), (429, 429), (503, 502)])
def test_http_errors_are_redacted(cloud, http, expected):
    cloud['http'] = http
    with pytest.raises(records_ai.RecordAIError) as error:
        run()
    assert error.value.status == expected
    assert 'secret' not in error.value.message


@pytest.mark.parametrize('code', ['20000003', '45000001', '55000031', None])
def test_failed_tasks_never_return_partial_text(cloud, code):
    cloud['code'] = code or ''
    with pytest.raises(records_ai.RecordAIError):
        run()
    cloud['code'] = '20000000'
    run()
    assert sum(r.url.path.endswith('/submit') for r in cloud['calls']) == 2


@pytest.mark.parametrize('change', [{'end_time': -1}, {'start_time': True}, {'text': None},
    {'additions': {'speaker': 'bad id'}}, {'words': [{'text': '坏', 'start_time': 0, 'end_time': 9999}]}])
def test_invalid_results_rejected(cloud, change):
    cloud['payload']['result']['utterances'][0].update(change)
    with pytest.raises(records_ai.RecordAIError):
        run()


def test_unknown_voice_and_zero_duration_word_remain_reviewable():
    data = payload(); first = data['result']['utterances'][0]
    first['additions'].pop('speaker')
    first['words'][0]['end_time'] = 0
    result = asr.normalize(data, 'task', 'scope')
    assert result['segments'][0]['speaker_id'] is None
    assert result['segments'][0]['voice_review'] is True
    assert result['segments'][0]['words'][0]['begin_time'] is None
    assert result['raw_result']['result']['utterances'][0]['words'][0]['end_time'] == 0


def test_incomplete_utterances_do_not_silently_drop_supplement():
    data = payload()
    data['result']['utterances'].pop()
    with pytest.raises(records_ai.RecordAIError):
        asr.normalize(data, 'task', 'scope')


def test_independent_credentials_public_redaction_and_legacy_save():
    current = Settings(); current.ai.speech.volcengine = config()
    current.ai.speech.api_key = SecretStr('aliyun-separate-secret')
    visible = public_settings(current)
    assert 'speech-test-secret' not in json.dumps(visible) and 'tos-test-secret' not in json.dumps(visible)
    assert visible['ai']['speech']['volcengine']['api_key_configured']
    incoming = persisted_settings(current)
    incoming['ai']['speech'] = {'provider': 'siliconflow'}
    assert merge_settings(SettingsUpdate.model_validate(incoming), current).ai.speech.volcengine == config()
    incoming['ai']['speech'] = {'provider': 'volcengine', 'volcengine': {'tos_bucket': 'test-bucket'}}
    merged = merge_settings(SettingsUpdate.model_validate(incoming), current)
    assert merged.ai.speech.volcengine.api_key == config().api_key
    assert reveal_key(merged.ai.speech.api_key) == 'aliyun-separate-secret'
    incoming['ai']['speech']['volcengine']['clear_api_key'] = True
    merged = merge_settings(SettingsUpdate.model_validate(incoming), current)
    assert not reveal_key(merged.ai.speech.volcengine.api_key)
    assert merged.ai.speech.volcengine.tos_secret_key == config().tos_secret_key


def reveal_key(value):
    return value.get_secret_value()


def test_no_fallback_or_local_model_requirement(cloud, monkeypatch):
    from backend.app import record_voice_audio
    settings = Settings(); settings.ai.enabled = True; settings.ai.speech.provider = 'volcengine'
    with pytest.raises(records_ai.RecordAIError):
        records_ai.provider_config(settings, 'asr')
    settings.ai.speech.volcengine = config()
    monkeypatch.setattr(record_voice_audio, 'available', lambda: False)
    monkeypatch.setattr(records_ai, 'normalize_for_asr', lambda raw, fmt, **kw: (raw, fmt, 'audio/mpeg'))
    result = records_ai.transcribe_audio(b'fixture audio', 'mp3', 'audio/mpeg', settings, speaker_count=2)
    assert result['segments'][-1]['text'] == '还有信息。'


@pytest.mark.parametrize('values', [{'tos_region': 'https://evil.test'}, {'tos_bucket': '../private'}])
def test_storage_config_rejects_arbitrary_endpoints(values):
    with pytest.raises(ValidationError):
        VolcengineSettings(**values)


def test_actual_route_stores_native_evidence_and_capability_without_local_models(client, cloud, monkeypatch):
    from backend.app import record_voice_audio
    settings = client.app.state.store.current
    settings.ai.enabled = True
    settings.ai.speech.provider = 'volcengine'
    settings.ai.speech.volcengine = config()
    monkeypatch.setattr(record_voice_audio, 'available', lambda: False)
    monkeypatch.setattr(records_ai, 'normalize_for_asr', lambda *args, **kw: (b'fixture audio', 'mp3', 'audio/mpeg'))
    caps = client.get('/api/records/capabilities').json()['ai']['asr']
    assert caps['ready'] and caps['speaker_count_available'] and caps['provider'] == 'volcengine'
    row = upload(client, create(client), duration=8000).json()
    response = client.post(f'/api/records/{row["id"]}/audio/{row["audio_clips"][0]["id"]}/transcribe',
        json={'expected_revision': row['revision'], 'confirmed': True, 'speaker_count': 2})
    assert response.status_code == 200, response.text
    saved = client.get(f'/api/records/{row["id"]}').json()
    assert [s['text'] for s in saved['segments']] == ['你还有补充吗？', '有。', '还有信息。']
    assert saved['segments'][-1]['speaker'] == 'unknown'
    assert saved['segments'][-1]['speaker_id'] == '1'
    assert saved['segments'][-1]['start_ms'] == 4000
