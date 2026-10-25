from io import BytesIO
import hashlib
import json
from types import SimpleNamespace
from zipfile import ZipFile

from fastapi import FastAPI
from backend.tests.auth_client import TestClient
import httpx
from pydantic import SecretStr
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app import records, records_ai
from backend.app.config import SettingsStore
from backend.app.database import AuditEvent, Base
from backend.app.people import Person, router as people_router
from backend.app.record_media import RecordMediaError

MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x10" + b"synthetic-audio-for-mock-tests" * 4
KEY = "test-records-provider-credential-only"


@pytest.fixture(autouse=True)
def cloud(monkeypatch):
    state = SimpleNamespace(calls=[], respond=lambda request: httpx.Response(503, json={"error": "unconfigured mock"}))
    real_client = httpx.Client
    def handle(request):
        state.calls.append(request)
        return state.respond(request)
    def factory(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return real_client(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(records_ai.httpx, 'Client', factory)
    return state


@pytest.fixture
def client(tmp_path):
    application = FastAPI()
    application.state.engine = create_engine(f"sqlite:///{tmp_path / 'records.sqlite3'}", connect_args={'check_same_thread': False})
    Base.metadata.create_all(application.state.engine)
    application.state.store = SettingsStore(tmp_path)
    application.state.store.load()
    application.include_router(people_router)
    application.include_router(records.router)
    with TestClient(application) as instance:
        yield instance
    application.state.engine.dispose()


def create(client, **changes):
    response = client.post('/api/records', json={'title': '虚构询问记录', 'scenario': 'residence', **changes})
    assert response.status_code == 201, response.text
    return response.json()


def upload(client, row, raw=MP3, filename='人物姓名不得外发.mp3', duration=5000):
    data = {'expected_revision': str(row['revision'])}
    if duration is not None:
        data['duration_ms'] = str(duration)
    return client.post(f"/api/records/{row['id']}/audio", data=data, files={'file': (filename, raw, 'application/octet-stream')})


def with_audio(client, **changes):
    row = create(client, **changes)
    response = upload(client, row)
    assert response.status_code == 201, response.text
    return response.json()


def segments(client, row, values):
    return client.patch(f"/api/records/{row['id']}/segments", json={'expected_revision': row['revision'], 'segments': values})


def with_segments(client, **changes):
    response = segments(client, create(client, **changes), [{'text': '我在测试日期到达，仅为合成陈述。', 'speaker': 'interviewee'}])
    assert response.status_code == 200, response.text
    return response.json()


def editable(segment, **changes):
    return {**{key: segment[key] for key in ('id', 'audio_id', 'start_ms', 'end_ms', 'speaker', 'text')}, **changes}


def enable_ai(client, provider='siliconflow'):
    with client.app.state.store.lock:
        settings = client.app.state.store.current
        settings.ai.enabled = True
        settings.ai.default_provider = provider
        configured = getattr(settings.ai.providers, provider)
        configured.enabled = True
        configured.api_key = SecretStr(KEY)


def asr(client, row, confirmed=True):
    return client.post(f"/api/records/{row['id']}/audio/{row['audio_clips'][0]['id']}/transcribe", json={'expected_revision': row['revision'], 'confirmed': confirmed})


def analyze(client, row, confirmed=True):
    return client.post(f"/api/records/{row['id']}/analyze", json={'expected_revision': row['revision'], 'confirmed': confirmed})


def analysis_response(identifier, **changes):
    output = {'summary': '合成材料的简要整理。', 'key_facts': [{'label': '到达陈述', 'value': '记录含到达陈述，日期待核对。', 'segment_ids': [identifier]}], 'issues': ['具体日期待核对'], **changes}
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(output, ensure_ascii=False)}}]})


def test_crud_person_snapshot_templates_revision_and_pagination(client):
    person = client.post('/api/people', json={'name': '虚构关联人', 'nationality': '示例', 'document_number': 'RECORD-PERSON', 'source': '合成资料'}).json()
    row = create(client, person_id=person['id'], occurred_at='2026-10-21T09:30:00+08:00')
    assert row['revision'] == 1 and row['template_id'] == 'residence'
    persisted = client.get(f"/api/records/{row['id']}").json()
    assert persisted['occurred_at'] == '2026-10-21T01:30:00+00:00'
    assert '2026-10-21T09:30:00+08:00' in persisted['rendered_text']
    assert row['person_snapshot']['name'] == person['name']
    with Session(client.app.state.engine) as session, session.begin():
        session.get(Person, person['id']).name = '后来修改的合成名称'
    assert client.get(f"/api/records/{row['id']}").json()['person_name'] == '虚构关联人'
    changed = client.patch(f"/api/records/{row['id']}", json={'expected_revision': 1, 'title': '改名后的笔录', 'template_id': 'study'})
    assert changed.status_code == 200 and changed.json()['revision'] == 2
    assert changed.json()['template_snapshot']['id'] == 'study'
    scenario = client.patch(f"/api/records/{row['id']}", json={'expected_revision': 2, 'scenario': 'marriage'}).json()
    assert scenario['template_id'] == scenario['template_snapshot']['id'] == 'marriage'
    assert scenario['revision'] == 3
    old = client.get(f"/api/records/{row['id']}/revisions/1").json()['snapshot']
    assert old['title'] == '虚构询问记录' and old['template_snapshot']['id'] == 'residence'
    assert client.patch(f"/api/records/{row['id']}", json={'expected_revision': 1, 'title': '陈旧修改'}).status_code == 409
    assert client.get('/api/records?q=虚构关联人&page_size=1').json()['total'] == 1
    assert client.get('/api/records?q=%25').json()['total'] == 0
    assert client.post('/api/records', json={'title': '时间无时区', 'occurred_at': '2026-10-21T09:00:00'}).status_code == 422
    assert client.patch(f"/api/records/{row['id']}", json={'expected_revision': 2, 'person_id': None}).status_code == 422
    assert client.get('/api/records/missing').status_code == 404
    caps = client.get('/api/records/capabilities').json()
    assert len(caps['templates']) == 5 and not caps['ai']['asr']['ready']
    assert 'api_key' not in json.dumps(caps)


def test_audio_preserved_hash_range_ownership_and_no_delete(client):
    row = with_audio(client)
    clip = row['audio_clips'][0]
    assert row['revision'] == 2 and clip['sha256'] == hashlib.sha256(MP3).hexdigest()
    assert clip['size_bytes'] == len(MP3) and clip['duration_source'] == 'declared'
    path = clip['playback_url']
    assert client.get(path).content == MP3
    part = client.get(path, headers={'Range': 'bytes=3-9'})
    assert part.status_code == 206 and part.content == MP3[3:10]
    assert part.headers['content-range'] == f'bytes 3-9/{len(MP3)}'
    assert client.get(path, headers={'Range': 'bytes=-6'}).content == MP3[-6:]
    assert client.get(path, headers={'Range': 'bytes=9-'}).content == MP3[9:]
    for invalid in ('bytes=999999-', 'bytes=9-2', 'bytes=0-1,5-9', 'bytes=-0', 'items=1-2'):
        assert client.get(path, headers={'Range': invalid}).status_code == 416
    assert 'attachment' in client.get(path + '?download=true').headers['content-disposition']
    other = create(client)
    assert client.get(f"/api/records/{other['id']}/audio/{clip['id']}/file").status_code == 404
    assert client.delete(path).status_code == 405
    assert client.get(f"/api/records/{row['id']}/revisions/2").json()['snapshot']['audio_clips'][0]['sha256'] == clip['sha256']


@pytest.mark.parametrize('raw,filename', [(b'<html>not audio</html>', 'fake.mp3'), (b'https://example.com/audio.mp3', 'url.mp3'), (b'no-independent-webm-header' * 3, 'slice.webm')])
def test_audio_rejects_extension_spoof_external_links_and_headerless_chunks(client, raw, filename):
    row = create(client)
    assert upload(client, row, raw, filename).status_code == 422
    assert client.get(f"/api/records/{row['id']}").json()['audio_count'] == 0


def test_signature_accepts_browser_mp4_audio_but_not_video():
    def box(kind, payload):
        return (8 + len(payload)).to_bytes(4, 'big') + kind + payload
    header = box(b'ftyp', b'isom' + b'\0' * 4 + b'iso2')
    def track(handler):
        return box(b'trak', box(b'mdia', box(b'hdlr', b'\0' * 8 + handler + b'\0' * 8)))
    audio = header + box(b'moov', track(b'soun'))
    assert records.detect_audio(audio) == ('m4a', 'audio/mp4')
    with pytest.raises(Exception) as error:
        records.detect_audio(header + box(b'moov', track(b'vide')))
    assert error.value.status_code == 422
    assert not records.mp4_has_audio_track(header + box(b'moov', track(b'soun') + track(b'vide')))


def test_upload_size_count_and_transaction_rollback(client, monkeypatch):
    row = create(client)
    monkeypatch.setitem(records.LIMITS, 'max_audio_bytes', 100)
    assert upload(client, row).status_code == 413
    monkeypatch.setitem(records.LIMITS, 'max_audio_bytes', 20 * 1024 * 1024)
    def fail(*args, **kwargs):
        raise SQLAlchemyError('secret SQL diagnostic never expose')
    monkeypatch.setattr(records, 'audit', fail)
    response = upload(client, row)
    assert response.status_code == 503 and 'secret SQL' not in response.text
    current = client.get(f"/api/records/{row['id']}").json()
    assert current['audio_count'] == 0 and current['revision'] == 1
    assert len(current['history']) == 1


def test_audio_per_record_limits_and_null_duration(client, monkeypatch):
    row = create(client)
    uploaded = upload(client, row, duration=None).json()
    assert uploaded['audio_clips'][0]['duration_ms'] is None
    assert uploaded['audio_clips'][0]['duration_source'] == 'unknown'
    monkeypatch.setitem(records.LIMITS, 'max_clips', 1)
    assert upload(client, uploaded).status_code == 413
    monkeypatch.setitem(records.LIMITS, 'max_clips', 30)
    monkeypatch.setitem(records.LIMITS, 'max_record_audio_bytes', len(MP3))
    assert upload(client, uploaded).status_code == 413


def test_segments_validate_ranges_and_ids_keep_revision_snapshots(client):
    row = with_audio(client)
    clip = row['audio_clips'][0]['id']
    for invalid in [
        {'text': ' '}, {'text': '字' * 20001}, {'text': '内容', 'speaker': 'invented'},
        {'text': '内容', 'start_ms': 0, 'end_ms': 100},
        {'text': '内容', 'audio_id': clip, 'start_ms': 0},
        {'text': '内容', 'audio_id': clip, 'start_ms': 100, 'end_ms': 100},
        {'text': '内容', 'audio_id': clip, 'start_ms': 0, 'end_ms': 5001},
        {'text': '内容', 'audio_id': 'other-record-clip'},
        {'id': 'invented-id', 'text': '内容'},
    ]:
        assert segments(client, row, [invalid]).status_code == 422
    response = segments(client, row, [{'text': '第一版人工内容', 'speaker': 'interviewee', 'audio_id': clip, 'start_ms': 0, 'end_ms': 1500}])
    assert response.status_code == 200, response.text
    first = response.json()
    segment = first['segments'][0]
    assert segment['timestamp_source'] == 'manual' and segment['source'] == 'manual'
    assert segments(client, first, [editable(segment), editable(segment)]).status_code == 422
    second = segments(client, first, [editable(segment, text='第二版人工内容')]).json()
    assert second['segments'][0]['id'] == segment['id'] and second['revision'] == first['revision'] + 1
    snapshot = client.get(f"/api/records/{row['id']}/revisions/{first['revision']}").json()['snapshot']
    assert snapshot['segments'][0]['text'] == '第一版人工内容'
    cleared = segments(client, second, []).json()
    assert cleared['segments'] == [] and cleared['audio_count'] == 1
    assert client.get(cleared['audio_clips'][0]['playback_url']).content == MP3


def test_ai_requires_confirmation_enabled_provider_and_key_before_outbound(client, cloud):
    row = with_audio(client)
    assert asr(client, row, False).status_code == 400
    assert asr(client, row).status_code == 409
    with client.app.state.store.lock:
        client.app.state.store.current.ai.enabled = True
    assert asr(client, row).status_code == 409
    enable_ai(client)
    with client.app.state.store.lock:
        client.app.state.store.current.ai.providers.siliconflow.enabled = False
    assert asr(client, row).status_code == 409
    assert cloud.calls == []
    assert client.get(f"/api/records/{row['id']}").json()['transcription_attempts'] == []


def test_asr_only_sends_selected_original_and_keeps_immutable_attempts(client, cloud):
    enable_ai(client)
    person = client.post('/api/people', json={'name': '不得外发的关联姓名', 'nationality': '示例', 'document_number': 'DO-NOT-SEND-DOC', 'source': '测试资料'}).json()
    row = with_audio(client, person_id=person['id'], location='不得外发的地点', interviewers='不得外发的询问人')
    cloud.respond = lambda request: httpx.Response(200, json={'text': '这是第一次原始转写。'})
    response = asr(client, row)
    assert response.status_code == 200, response.text
    updated = response.json()
    outbound = cloud.calls[0]
    assert str(outbound.url) == records_ai.ASR_URL
    assert outbound.headers['authorization'] == f'Bearer {KEY}'
    body = outbound.read()
    assert MP3 in body and b'filename="recording.mp3"' in body
    for value in (person['name'], person['document_number'], row['location'], row['interviewers'], row['audio_clips'][0]['filename']):
        assert value.encode() not in body
    segment = updated['segments'][0]
    assert (segment['speaker'], segment['source'], segment['start_ms'], segment['end_ms']) == ('unknown', 'asr', None, None)
    assert segment['timestamp_source'] == 'none'
    revised = segments(client, updated, [editable(segment, text='人工核实后修订。')]).json()
    cloud.respond = lambda request: httpx.Response(200, json={'text': '第二次独立原始转写。'})
    again = asr(client, revised).json()
    assert [item['text'] for item in again['segments']] == ['人工核实后修订。', '第二次独立原始转写。']
    assert {item['text'] for item in again['transcription_attempts']} == {'这是第一次原始转写。', '第二次独立原始转写。'}
    assert client.get(again['audio_clips'][0]['playback_url']).content == MP3


@pytest.mark.parametrize('result,expected', [
    (lambda: httpx.Response(401, json={'detail': KEY}), 400),
    (lambda: httpx.Response(429, json={'detail': KEY}), 429),
    (lambda: httpx.Response(307, headers={'location': 'https://other.example/'}), 502),
    (lambda: httpx.Response(200, json={'text': KEY}), 502),
    (lambda: httpx.Response(200, json={'text': ' '}), 502),
    (lambda: httpx.Response(200, content=b'x' * (128 * 1024 + 1)), 502),
])
def test_asr_failures_are_sanitized_recorded_and_do_not_modify_segments(client, cloud, result, expected):
    enable_ai(client)
    row = with_audio(client)
    cloud.respond = lambda request: result()
    response = asr(client, row)
    assert response.status_code == expected and KEY not in response.text
    current = client.get(f"/api/records/{row['id']}").json()
    assert current['segments'] == [] and current['revision'] == row['revision']
    assert current['transcription_attempts'][0]['status'] == 'failed'
    assert KEY not in json.dumps(current)
    assert len(cloud.calls) == 1


def test_asr_revision_changed_during_outbound_keeps_result_without_applying(client, cloud):
    enable_ai(client)
    row = with_audio(client)
    def respond(request):
        # A second transaction succeeds while the cloud call is in progress:
        # proves the outbound request does not hold the database write lock.
        with records.write_session(client.app.state.engine) as session:
            current = records.get_record(session, row['id'], True)
            current.title = '请求期间已经修改'
            records.version(session, current, 'updated')
        return httpx.Response(200, json={'text': '仍需保留的原始返回。'})
    cloud.respond = respond
    assert asr(client, row).status_code == 409
    current = client.get(f"/api/records/{row['id']}").json()
    assert current['segments'] == [] and current['title'] == '请求期间已经修改'
    assert current['transcription_attempts'][0]['status'] == 'stale'
    assert current['transcription_attempts'][0]['text'] == '仍需保留的原始返回。'


def test_analysis_outbound_fields_grounded_snapshot_staleness_and_export(client, cloud):
    enable_ai(client, 'deepseek')
    row = with_segments(client, location='不要外发地点', interviewers='不要外发经办人')
    identifier = row['segments'][0]['id']
    cloud.respond = lambda request: analysis_response(identifier)
    assert analyze(client, row, False).status_code == 400
    response = analyze(client, row)
    assert response.status_code == 200, response.text
    current = response.json()
    payload = json.loads(cloud.calls[0].read())
    assert str(cloud.calls[0].url) == records_ai.CHAT_URLS['deepseek']
    assert json.loads(payload['messages'][1]['content']) == [{'segment_id': identifier, 'text': row['segments'][0]['text']}]
    assert '不要外发' not in json.dumps(payload, ensure_ascii=False)
    analysis = current['analysis']
    assert analysis['stale'] is False and analysis['input_revision'] == row['revision']
    assert analysis['key_facts'][0]['segment_ids'] == [identifier]
    assert analysis['input_segments'][0]['text'] == row['segments'][0]['text']
    exported = client.get(f"/api/records/{row['id']}/export.txt?include_ai=true")
    assert exported.status_code == 200 and 'AI摘要附录' in exported.text
    assert analysis['id'] in exported.text and identifier in exported.text
    revised = segments(client, current, [editable(current['segments'][0], text='人工修订后的新事实。')]).json()
    assert revised['analysis']['stale'] is True
    assert revised['analysis']['input_segments'][0]['text'] == row['segments'][0]['text']
    assert client.get(f"/api/records/{row['id']}/export.txt?include_ai=true").status_code == 409
    plain = client.get(f"/api/records/{row['id']}/export.txt")
    assert plain.status_code == 200 and 'AI摘要附录' not in plain.text and '人工修订后的新事实。' in plain.text


@pytest.mark.parametrize('mode', ['bad_reference', 'length', 'extra_field', 'secret', 'blank_summary'])
def test_analysis_rejects_unverifiable_or_incomplete_outputs(client, cloud, mode):
    enable_ai(client)
    row = with_segments(client)
    identifier = row['segments'][0]['id']
    def respond(request):
        if mode == 'bad_reference':
            return analysis_response('invented-segment')
        if mode == 'extra_field':
            return analysis_response(identifier, invented='ignored?')
        if mode == 'secret':
            return analysis_response(identifier, summary=KEY)
        if mode == 'blank_summary':
            return analysis_response(identifier, summary=' ')
        payload = json.loads(analysis_response(identifier).content)
        payload['choices'][0]['finish_reason'] = 'length'
        return httpx.Response(200, json=payload)
    cloud.respond = respond
    response = analyze(client, row)
    assert response.status_code == 502 and KEY not in response.text
    current = client.get(f"/api/records/{row['id']}").json()
    assert current['analysis'] is None and current['revision'] == row['revision']
    assert current['analyses'][0]['status'] == 'failed'


def test_analysis_changed_during_call_is_not_applied(client, cloud):
    enable_ai(client)
    row = with_segments(client)
    def respond(request):
        with records.write_session(client.app.state.engine) as session:
            current = records.get_record(session, row['id'], True)
            records.ordered_segments(session, row['id'])[0].text = '并发修订的新正文。'
            records.version(session, current, 'segments_updated')
        return analysis_response(row['segments'][0]['id'])
    cloud.respond = respond
    assert analyze(client, row).status_code == 409
    current = client.get(f"/api/records/{row['id']}").json()
    assert current['analysis'] is None and current['analyses'][0]['status'] == 'stale'
    assert current['analyses'][0]['input_segments'][0]['text'] == row['segments'][0]['text']
    assert current['segments'][0]['text'] == '并发修订的新正文。'


def test_review_freezes_rendered_body_archive_restore_cannot_unlock_and_exports(client, monkeypatch):
    empty = create(client)
    request = {'expected_revision': empty['revision'], 'status': 'reviewed', 'confirmed': True, 'review_note': '人工核对'}
    assert client.patch(f"/api/records/{empty['id']}", json=request).status_code == 422
    row = with_audio(client)
    row = segments(client, row, [{'text': '已人工修订正文。', 'speaker': 'interviewee'}]).json()
    for extra in ({'confirmed': False}, {'review_note': ''}):
        assert client.patch(f"/api/records/{row['id']}", json={**request, 'expected_revision': row['revision'], **extra}).status_code == 422
    reviewed = client.patch(f"/api/records/{row['id']}", json={**request, 'expected_revision': row['revision']}).json()
    assert reviewed['status'] == 'reviewed' and reviewed['revision'] == row['revision'] + 1
    frozen = reviewed['rendered_text']
    monkeypatch.setattr(records, 'TEMPLATES', [{**item, 'name': '后来程序更新的模板名'} for item in records.TEMPLATES])
    monkeypatch.setattr(records, 'SPEAKERS', {key: '程序新版的角色文字' for key in records.SPEAKERS})
    assert client.get(f"/api/records/{row['id']}").json()['rendered_text'] == frozen
    assert segments(client, reviewed, [{'text': '不得写入'}]).status_code == 409
    assert upload(client, reviewed).status_code == 409
    assert client.patch(f"/api/records/{row['id']}", json={'expected_revision': reviewed['revision'], 'status': 'draft'}).status_code == 409
    txt = client.get(f"/api/records/{row['id']}/export.txt")
    assert '人工核对稿·未签发' in txt.text and frozen in txt.text
    docx = client.get(f"/api/records/{row['id']}/export.docx")
    with ZipFile(BytesIO(docx.content)) as package:
        assert '未签发' in package.read('word/document.xml').decode()
        assert '已人工修订正文' in package.read('word/document.xml').decode()
    archived = client.patch(f"/api/records/{row['id']}", json={'expected_revision': reviewed['revision'], 'status': 'archived'}).json()
    assert archived['archived_from'] == 'reviewed'
    assert client.get(f"/api/records/{row['id']}/export.txt").status_code == 409
    assert client.get(archived['audio_clips'][0]['playback_url']).content == MP3
    assert client.patch(f"/api/records/{row['id']}", json={'expected_revision': archived['revision'], 'status': 'draft'}).status_code == 409
    restored = client.patch(f"/api/records/{row['id']}", json={'expected_revision': archived['revision'], 'status': 'reviewed'}).json()
    assert restored['status'] == 'reviewed' and restored['rendered_text'] == frozen
    assert client.get(f"/api/records/{row['id']}/export.txt").status_code == 200


def test_audit_contains_identifiers_counts_only(client, cloud):
    enable_ai(client)
    row = with_audio(client)
    cloud.respond = lambda request: httpx.Response(200, json={'text': '不允许出现在审计详情中的原始转写'})
    current = asr(client, row).json()
    with Session(client.app.state.engine) as session:
        events = session.scalars(select(AuditEvent).where(AuditEvent.action.like('records.%'))).all()
        assert events
        for item in events:
            assert set(item.details) <= {'record_id', 'revision', 'audio_id', 'attempt_id', 'segment_count'}
            text = json.dumps(item.details, ensure_ascii=False)
            assert KEY not in text and current['segments'][0]['text'] not in text
            assert row['title'] not in text and row['audio_clips'][0]['filename'] not in text


def test_interrupted_attempt_recovery_marks_failure_without_reissuing_requests(client, cloud):
    row = with_audio(client)
    with Session(client.app.state.engine) as session, session.begin():
        session.add(records.TranscriptionAttempt(record_id=row['id'], audio_id=row['audio_clips'][0]['id'], provider='siliconflow', model='mock-asr', input_revision=row['revision']))
        session.add(records.RecordAnalysis(record_id=row['id'], provider='deepseek', model='mock-chat', input_revision=row['revision'], input_segments=[], input_hash=records.content_hash([])))
    assert records.recover_interrupted_attempts(client.app.state.engine) == {'transcriptions': 1, 'analyses': 1}
    assert records.recover_interrupted_attempts(client.app.state.engine) == {'transcriptions': 0, 'analyses': 0}
    current = client.get(f"/api/records/{row['id']}").json()
    assert current['transcription_attempts'][0]['status'] == current['analyses'][0]['status'] == 'failed'
    assert '未自动重试' in current['analyses'][0]['error']
    assert current['revision'] == row['revision'] and cloud.calls == []
    with Session(client.app.state.engine) as session:
        event = session.scalars(select(AuditEvent).where(AuditEvent.action == 'records.interrupted_recovered')).one()
        assert event.details == {'transcription_count': 1, 'analysis_count': 1}


def test_nontext_segment_changes_also_mark_summary_stale(client, cloud):
    enable_ai(client)
    row = with_segments(client)
    cloud.respond = lambda request: analysis_response(row['segments'][0]['id'])
    analyzed = analyze(client, row).json()
    assert analyzed['analysis']['stale'] is False
    changed = segments(client, analyzed, [editable(analyzed['segments'][0], speaker='interpreter')]).json()
    assert changed['segments'][0]['text'] == row['segments'][0]['text']
    assert changed['analysis']['stale'] is True


def test_conversion_failure_preserves_original_and_success_sends_only_local_mp3(client, cloud, monkeypatch):
    enable_ai(client)
    # The local signature check is not claimed to be a complete codec decoder.
    wav = b'RIFF' + b'\0' * 4 + b'WAVEfmt ' + b'\0' * 16 + b'data' + b'original-wav-payload'
    response = upload(client, create(client), wav, 'private-original-name.wav')
    assert response.status_code == 201
    row = response.json()
    def unavailable(*args):
        raise RecordMediaError('本地转换不可用，原件已保留，尚未发送。')
    monkeypatch.setattr(records_ai, 'normalize_for_asr', unavailable)
    assert asr(client, row).status_code == 422
    current = client.get(f"/api/records/{row['id']}").json()
    assert current['transcription_attempts'][0]['status'] == 'failed'
    assert cloud.calls == [] and client.get(current['audio_clips'][0]['playback_url']).content == wav
    monkeypatch.setattr(records_ai, 'normalize_for_asr', lambda raw, format: (MP3, 'mp3', 'audio/mpeg'))
    cloud.respond = lambda request: httpx.Response(200, json={'text': '经本地转换后转写的模拟结果。'})
    assert asr(client, current).status_code == 200
    payload = cloud.calls[0].read()
    assert MP3 in payload and wav not in payload and b'private-original-name' not in payload
    assert client.get(current['audio_clips'][0]['playback_url']).content == wav


def test_asr_declared_hour_limit_and_analysis_text_limit_prevent_outbound(client, cloud):
    enable_ai(client)
    row = upload(client, create(client), duration=3600001).json()
    assert asr(client, row).status_code == 422
    text_row = segments(client, create(client), [{'text': '甲' * 20000}, {'text': '乙' * 10001}]).json()
    assert analyze(client, text_row).status_code == 422
    assert cloud.calls == []


def test_bounded_stream_stops_reading_and_timeout_does_not_expose_diagnostics(client, cloud):
    enable_ai(client)
    row = with_audio(client)
    consumed = []
    class TooLarge(httpx.SyncByteStream):
        def __iter__(self):
            consumed.append(1)
            yield b'x' * (64 * 1024)
            consumed.append(2)
            yield b'x' * (80 * 1024)
            raise AssertionError('Must not read beyond the response byte cap')
    cloud.respond = lambda request: httpx.Response(200, stream=TooLarge())
    assert asr(client, row).status_code == 502
    assert consumed == [1, 2]
    def timeout(request):
        raise httpx.ReadTimeout('upstream diagnostic containing ' + KEY)
    cloud.respond = timeout
    response = asr(client, row)
    assert response.status_code == 504 and KEY not in response.text
    assert all(item['status'] == 'failed' for item in client.get(f"/api/records/{row['id']}").json()['transcription_attempts'])


def test_encoded_credential_echo_is_rejected_after_json_decoding(client, cloud):
    enable_ai(client)
    row = with_segments(client)
    # The nested JSON encodes its first character so it is absent as a raw
    # substring in the HTTP body; the decoded model output must still reject it.
    raw = json.dumps({'summary': KEY, 'key_facts': [], 'issues': []}).replace(KEY, '\\u0074' + KEY[1:])
    cloud.respond = lambda request: httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': raw}}]})
    response = analyze(client, row)
    assert response.status_code == 502 and KEY not in response.text
    current = client.get(f"/api/records/{row['id']}").json()
    assert current['analysis'] is None and KEY not in json.dumps(current)


def test_same_revision_concurrent_edit_is_rejected_without_losing_saved_text(client):
    from concurrent.futures import ThreadPoolExecutor
    row = with_segments(client)
    def modify(value):
        return segments(client, row, [editable(row['segments'][0], text=value)])
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(modify, ['并发甲内容', '并发乙内容']))
    assert sorted(result.status_code for result in results) == [200, 409]
    succeeded = next(result.json() for result in results if result.status_code == 200)
    current = client.get(f"/api/records/{row['id']}").json()
    assert current['segments'] == succeeded['segments']
    assert current['revision'] == row['revision'] + 1


def test_register_audio_duration_preserves_unknowns_and_text_only_records(client):
    row = create(client)
    assert row['audio_count'] == 0 and row['audio_duration_ms'] is None
    assert row['audio_unknown_duration_count'] == 0
    row = upload(client, row, duration=None).json()
    assert row['audio_count'] == 1 and row['audio_duration_ms'] is None
    assert row['audio_unknown_duration_count'] == 1
    row = upload(client, row, raw=MP3 + b'known', duration=65000).json()
    item = client.get('/api/records').json()['items'][0]
    assert item['audio_count'] == 2 and item['audio_duration_ms'] == 65000
    assert item['audio_unknown_duration_count'] == 1
    assert item['segment_count'] == 0
    row = segments(client, row, [{'text': '核对文字', 'speaker': 'interviewee'}]).json()
    assert row['segment_count'] == 1 and row['audio_duration_ms'] == 65000


def test_historical_local_summary_is_readable_but_not_an_ai_export(client, cloud):
    enable_ai(client)
    row = with_segments(client)
    cloud.respond = lambda _: analysis_response(row['segments'][0]['id'])
    analyzed = analyze(client, row).json()
    with Session(client.app.state.engine) as session:
        result = session.get(records.RecordAnalysis, analyzed['analysis']['id'])
        result.provider = 'local'
        session.commit()
    assert client.get('/api/records/'+row['id']).json()['analysis']['summary']
    assert client.get('/api/records/'+row['id']+'/export.txt').status_code == 200
    assert client.get('/api/records/'+row['id']+'/export.txt?include_ai=true').status_code == 409


def test_common_locations_available_without_changing_existing_records(client):
    row = client.post('/api/records', json={'title': '地点留存', 'location': '原地点'}).json()
    settings = client.app.state.store.load()
    settings.general.interview_locations = ['第一询问室', '第二询问室']
    client.app.state.store.save(settings)
    assert client.get('/api/records/capabilities').json()['interview_locations'] == ['第一询问室', '第二询问室']
    assert client.get(f"/api/records/{row['id']}").json()['location'] == '原地点'
