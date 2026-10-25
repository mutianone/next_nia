"""Exercise the offline workflow with all outbound processing disabled."""
import pytest
from backend.tests.auth_client import TestClient
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.app import records, knowledge
from backend.app.records import AudioClip


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('NEXT_NIA_PRESENTATION', '1')
    def no_cloud(*args, **kwargs):
        raise AssertionError('offline workflow attempted an external AI request')
    monkeypatch.setattr(records, 'transcribe_audio', no_cloud)
    monkeypatch.setattr(records, 'analyze_segments', no_cloud)
    monkeypatch.setattr(knowledge, 'answer_with_sources', no_cloud)
    with TestClient(create_app(tmp_path)) as instance:
        yield instance


def test_offline_end_to_end(client):
    assert client.get('/api/settings').json()['runtime']['local_processing'] is True
    dashboard = client.get('/api/dashboard').json()
    assert dashboard['stats']['people'] > 0
    response = client.post('/api/knowledge/ask', json={'question': '申请延长居留期限，应当何时提出申请？'})
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer['status'] == 'answered'
    assert answer['provider'] == 'local'
    assert all(source['allow_external'] is False for source in answer['sources'])
    ids = {source['id'] for source in answer['sources']}
    assert all(set(claim['source_ids']) <= ids for claim in answer['claims'])
    row = client.post('/api/records', json={'title': '停留情况询问', 'scenario': 'residence'}).json()
    base = '/api/records/' + row['id']
    response = client.post(base + '/audio-library', json={'expected_revision': row['revision']})
    assert response.status_code == 200, response.text
    row = response.json()
    assert row['audio_count'] == 1
    clip_id = row['audio_clips'][0]['id']
    assert client.get(row['audio_clips'][0]['playback_url']).status_code == 200
    # Loading the same library item is idempotent.
    again = client.post(base + '/audio-library', json={'expected_revision': row['revision']}).json()
    assert again['revision'] == row['revision'] and again['audio_count'] == 1
    response = client.post(base + '/audio/' + clip_id + '/transcribe', json={'expected_revision': row['revision']})
    assert response.status_code == 200, response.text
    row = response.json()
    assert len(row['segments']) == 6
    assert {part['speaker'] for part in row['segments']} == {'interviewer', 'interviewee'}
    response = client.post(base + '/analyze', json={'expected_revision': row['revision']})
    assert response.status_code == 200, response.text
    row = response.json()
    analysis = row['analysis']
    assert not analysis['stale'] and analysis['provider'] == 'local'
    assert len(analysis['key_facts']) == 3
    assert all('？' not in fact['value'] for fact in analysis['key_facts'])
    text_by_id = {part['id']: part['text'] for part in row['segments']}
    for fact in analysis['key_facts']:
        assert fact['value'] in text_by_id[fact['segment_ids'][0]]
    assert client.get(base + '/export.docx?include_ai=true').status_code == 200
    assert client.get('/api/dashboard').json()['stats']['transcript_drafts'] > dashboard['stats']['transcript_drafts']
    # Existing optimistic locking remains enforced.
    assert client.post(base + '/analyze', json={'expected_revision': 1}).status_code == 409


def test_unknown_audio_is_not_given_unrelated_transcript(client):
    row = client.post('/api/records', json={'title': '音频核对', 'scenario': 'residence'}).json()
    base = '/api/records/' + row['id']
    row = client.post(base + '/audio-library', json={'expected_revision': row['revision']}).json()
    clip_id = row['audio_clips'][0]['id']
    with Session(client.app.state.engine) as session:
        clip = session.get(AudioClip, clip_id)
        clip.raw_audio += b'changed'
        session.commit()
    response = client.post(base + '/audio/' + clip_id + '/transcribe', json={'expected_revision': row['revision']})
    assert response.status_code == 422
    row = client.get(base).json()
    assert row['segments'] == []
    assert row['transcription_attempts'][0]['status'] == 'failed'


def test_normal_mode_has_no_library_or_auto_seed(tmp_path, monkeypatch):
    monkeypatch.delenv('NEXT_NIA_PRESENTATION', raising=False)
    with TestClient(create_app(tmp_path)) as client:
        assert not client.get('/api/settings').json()['runtime']['local_processing']
        assert client.get('/api/dashboard').json()['stats']['people'] == 0
        assert not client.get('/api/records/capabilities').json()['ai']['asr']['ready']
        row = client.post('/api/records', json={'title': '询问', 'scenario': 'study'}).json()
        assert client.post('/api/records/' + row['id'] + '/audio-library', json={'expected_revision': row['revision']}).status_code == 404


def test_restart_preserves_work_without_duplicate_seed(tmp_path, monkeypatch):
    monkeypatch.setenv('NEXT_NIA_PRESENTATION', '1')
    with TestClient(create_app(tmp_path)) as client:
        count = client.get('/api/dashboard').json()['stats']['people']
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/dashboard').json()['stats']['people'] == count


def test_existing_business_directory_is_never_seeded(tmp_path, monkeypatch):
    monkeypatch.delenv('NEXT_NIA_PRESENTATION', raising=False)
    with TestClient(create_app(tmp_path)):
        pass
    original = (tmp_path / 'test.sqlite3').read_bytes()
    monkeypatch.setenv('NEXT_NIA_PRESENTATION', '1')
    from backend.app.config import ConfigurationError
    with pytest.raises(ConfigurationError, match='独立'):
        with TestClient(create_app(tmp_path)):
            pass
    assert (tmp_path / 'test.sqlite3').read_bytes() == original


def test_local_mode_disallows_cloud_connection_tests(client):
    response = client.post('/api/settings/test-ai', json={'provider':'siliconflow', 'chat_model':'test', 'api_key':'not-a-real-key'})
    assert response.status_code == 409
    assert not client.get('/api/settings').json()['settings']['ai']['enabled']


def test_missing_change_procedure_is_not_answered_with_extension_law(client):
    result = client.post('/api/knowledge/ask', json={'question': '居留证件事项发生变更，需要办理什么手续？'}).json()
    assert result['status'] == 'insufficient'
    assert result['claims'] == []


def test_residence_extension_does_not_include_employment_law(client):
    result = client.post('/api/knowledge/ask', json={'question': '申请延长居留期限，应当何时提出申请？'}).json()
    assert result['status'] == 'answered'
    assert result['sources']
    assert all('延长居留期限' in source['text'] for source in result['sources'])
