import hashlib
import json
import wave
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from unittest.mock import patch
import zipfile

import pytest
from backend.tests.auth_client import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.app.demo import DemoDataset
from backend.app.database import Base, AuditEvent
from backend.app.people import Person, PersonStay
from backend.app.records import AudioClip, RecordSession, RecordSegment
from backend.app.alerts import scan_alerts
from backend.app.maintenance import snapshot, restore_empty, verify_archive


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c: yield c


def seed(client):
    r=client.post('/api/demo-data/generate',json={'confirmed':True})
    assert r.status_code==200,r.text
    return r.json()


def clear(client,batch):
    return client.post('/api/demo-data/clear',json={'confirmed':True,'batch_id':batch['batch_id']})


def test_complete_dataset_is_local_coherent_and_repeatable(client):
    with patch('socket.create_connection',side_effect=AssertionError('No external requests')):
        data=seed(client)
    assert data['counts']['people']==36 and data['counts']['person_stays']==54
    assert data['counts']['record_sessions']==12 and data['counts']['record_audio']==12
    assert data['counts']['document_drafts']==16 and data['counts']['knowledge_documents']==4
    assert data['counts']['profile_relations']==12 and data['counts']['profile_facts']==72
    assert data['counts']['alerts']>10
    again=seed(client)
    assert not again['created'] and again['counts']==data['counts'] and again['batch_id']==data['batch_id']
    with Session(client.app.state.engine) as session:
        people=session.scalars(select(Person)).all()
        assert all(not any(marker in p.name+p.notes+p.source for marker in ['mock','测试','演示','虚构']) for p in people)
        for stay in session.scalars(select(PersonStay)):
            assert stay.permitted_until is None or stay.permitted_until>=stay.entry_date
            assert stay.exit_date is None or stay.exit_date>=stay.entry_date
        assert len({p.document_number for p in people})==36
    assert client.get('/api/dashboard').json()['stats']['people']==34
    report=client.get('/api/analytics').json()
    assert report['period']['entries']>=30 and report['stock']['people']==34
    assert client.post('/api/knowledge/search',json={'query':'住宿登记'}).status_code==200
    p=client.get('/api/people').json()['items'][0]
    assert client.get('/api/profiles/'+p['id']).json()['facts']


def test_record_audio_transcripts_summary_and_exports_are_consistent(client):
    seed(client)
    with Session(client.app.state.engine) as session:
        identifiers=session.scalars(select(RecordSession.id)).all()
        for clip in session.scalars(select(AudioClip)):
            assert clip.sha256==hashlib.sha256(clip.raw_audio).hexdigest()
            with wave.open(BytesIO(clip.raw_audio)) as wav:
                assert wav.getnframes()>16000*10
                assert abs(clip.duration_ms-wav.getnframes()/wav.getframerate()*1000)<2
    for identifier in identifiers:
        record=client.get('/api/records/'+identifier).json()
        assert len(record['segments'])==6 and record['analysis'] is None
        assert all(segment['source'] == 'manual' for segment in record['segments'])
        assert record['transcription_attempts'] == []
        assert client.get(f'/api/records/{identifier}/export.txt').status_code==200
    documents=client.get('/api/documents').json()['items']
    for doc in documents:
        detail=client.get('/api/documents/'+doc['id'])
        assert detail.status_code==200
        assert client.get('/api/documents/'+doc['id']+'/export.txt').status_code in (200,422)


def test_cleanup_preserves_existing_business_settings_templates_and_later_independent_records(client):
    original=client.post('/api/people',json={'name':'自有资料','nationality':'中国','document_number':'EXISTING-1','source':'人工录入'}).json()
    settings=client.get('/api/settings').json()['settings']
    original_templates=client.get('/api/documents/templates').json()
    batch=seed(client)
    scan_alerts(client.app.state.engine)
    extra=client.post('/api/people',json={'name':'后续自有资料','nationality':'中国','document_number':'EXISTING-2','source':'人工录入'}).json()
    result=clear(client,batch)
    assert result.status_code==200,result.text
    assert not result.json()['active']
    assert {x['id'] for x in client.get('/api/people').json()['items']}=={original['id'],extra['id']}
    assert client.get('/api/documents/templates').json()==original_templates
    assert client.get('/api/settings').json()['settings']==settings
    assert client.get('/api/records').json()['total']==0
    assert client.get('/api/analytics').json()['stock']['people']==2
    with Session(client.app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(AudioClip))==0
        assert session.scalar(select(func.count()).select_from(DemoDataset))==0
        assert session.scalar(select(AuditEvent).where(AuditEvent.action=='demo.cleared'))
    assert not clear(client,batch).json()['cleared']
    assert seed(client)['created']


def test_confirmations_production_guard_and_stale_cleanup(client):
    assert client.post('/api/demo-data/generate',json={}).status_code==422
    batch=seed(client)
    assert client.post('/api/demo-data/clear',json={'confirmed':True,'batch_id':'outdated'}).status_code==409
    client.app.state.active_settings.environment.mode='production'
    assert not client.get('/api/demo-data').json()['enabled']
    assert client.post('/api/demo-data/generate',json={'confirmed':True}).status_code==409
    assert clear(client,batch).status_code==409
    client.app.state.active_settings.environment.mode='test'
    assert client.get('/api/demo-data').json()['active']


def test_later_linked_record_and_children_are_deleted(client):
    batch=seed(client)
    p=client.get('/api/people').json()['items'][0]
    record=client.post('/api/records',json={'title':'另行录入的独立笔录','person_id':p['id']}).json()
    with Session(client.app.state.engine) as session:
        session.add(RecordSegment(record_id=record['id'],position=99,speaker='unknown',text='关联记录补充',source='manual'))
        session.commit()
    result=clear(client,batch)
    assert result.status_code==200,result.text
    assert client.get('/api/records/'+record['id']).status_code==404
    with Session(client.app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(RecordSegment)) == 0


def test_generated_record_lifecycle_children_are_cleaned(client):
    batch=seed(client)
    with Session(client.app.state.engine) as session:
        record=session.scalar(select(RecordSession))
        session.add(RecordSegment(record_id=record.id,position=99,speaker='unknown',text='后续补充文字',source='manual'))
        session.commit()
    assert clear(client,batch).status_code==200
    with Session(client.app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(RecordSegment))==0


def test_concurrent_generation_creates_one_batch(client):
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda _:seed(client),range(2)))
    assert sum(x['created'] for x in results)==1
    assert results[0]['batch_id']==results[1]['batch_id']


def test_generation_is_atomic_when_assets_fail(client):
    with patch('backend.app.demo.wave.open',side_effect=OSError('unavailable')):
        assert client.post('/api/demo-data/generate',json={'confirmed':True}).status_code==503
    with Session(client.app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(Person))==0
        assert session.get(DemoDataset,1) is None


def test_archive_roundtrip_retains_cleanup_ledger_and_accepts_previous_version(client,tmp_path):
    batch=seed(client); archive=tmp_path/'new.zip';snapshot(client.app.state.engine,archive)
    engine=create_engine('sqlite:///'+str(tmp_path/'restored.sqlite3'))
    restore_empty(engine,archive)
    with Session(engine) as session:
        assert session.get(DemoDataset,1).manifest['batch_id']==batch['batch_id']
    engine.dispose()
    assert clear(client,batch).status_code==200
    snapshot(client.app.state.engine,archive)
    old=tmp_path/'old.zip'
    with zipfile.ZipFile(archive) as src,zipfile.ZipFile(old,'w') as dst:
        manifest=json.loads(src.read('manifest.json'));manifest['tables'].pop('demo_datasets')
        for name in src.namelist():
            if name not in ['manifest.json','demo_datasets.jsonl']:dst.writestr(name,src.read(name))
        dst.writestr('manifest.json',json.dumps(manifest))
    assert 'demo_datasets' not in verify_archive(old)['tables']
    legacy_engine=create_engine('sqlite:///'+str(tmp_path/'legacy.sqlite3'))
    restore_empty(legacy_engine,old)
    with Session(legacy_engine) as session:assert session.get(DemoDataset,1) is None
    legacy_engine.dispose()


def test_cleanup_follows_copy_source_and_citation_links_without_deleting_unrelated_people(client):
    from backend.app.documents import DocumentDraft
    from backend.app.profiles import ProfileFact, ProfileRelation
    from backend.app.assistant_history import AssistantMessage, AssistantConversation
    from backend.app.knowledge import KnowledgeDocument
    batch = seed(client)
    independent = client.post('/api/people',json={'name':'保留档案','nationality':'中国','document_number':'KEEP-001','source':'人工录入'}).json()
    with Session(client.app.state.engine) as session:
        original = session.scalar(select(DocumentDraft))
        demo_person = original.person_id
        copy = DocumentDraft(person_id=independent['id'], template_id=original.template_id,
            copied_from=original.id, title='跨档案关联副本', fields={}, template_snapshot={}, person_snapshot={})
        session.add(copy); session.flush()
        second = DocumentDraft(person_id=independent['id'], template_id=original.template_id,
            copied_from=copy.id, title='二级关联副本', fields={}, template_snapshot={}, person_snapshot={})
        session.add(second); session.flush()
        fact = ProfileFact(person_id=independent['id'],kind='purpose',label='关联来源',value='待核实',source_type='document',source_id=second.id,source='文书',source_snapshot={})
        session.add(fact)
        law = session.scalar(select(KnowledgeDocument))
        session.add(AssistantConversation(id='mixed',owner_id='test-owner',title='保留会话',updated_at='2026-10-26'))
        session.add(AssistantMessage(id='linked-turn',conversation_id='mixed',ordinal=1,data=json.dumps({'answer':{'sources':[{'document_id':law.id}]}}),status='complete'))
        session.add(AssistantMessage(id='keep-turn',conversation_id='mixed',ordinal=2,data=json.dumps({'question':'独立问题'}),status='complete'))
        session.commit()
        copy_id, second_id, fact_id = copy.id, second.id, fact.id
    assert clear(client,batch).status_code == 200
    with Session(client.app.state.engine) as session:
        assert session.get(Person,independent['id']) is not None
        assert session.get(Person,demo_person) is None
        assert session.get(DocumentDraft,copy_id) is None
        assert session.get(DocumentDraft,second_id) is None
        assert session.get(ProfileFact,fact_id) is None
        assert session.get(AssistantMessage,'linked-turn') is None
        assert session.get(AssistantMessage,'keep-turn') is not None
        assert session.get(AssistantConversation,'mixed') is not None


def test_related_cleanup_rolls_back_every_delete_on_failure(client, monkeypatch):
    batch = seed(client)
    original_execute = Session.execute
    deletions = 0
    def fail_during_delete(self, statement, *args, **kwargs):
        nonlocal deletions
        if getattr(statement, 'is_delete', False):
            deletions += 1
            if deletions == 2:
                raise RuntimeError('test cleanup interruption')
        return original_execute(self, statement, *args, **kwargs)
    monkeypatch.setattr(Session, 'execute', fail_during_delete)
    with pytest.raises(RuntimeError, match='test cleanup interruption'):
        clear(client, batch)
    assert client.get('/api/demo-data').json()['counts'] == batch['counts']
    assert client.get('/api/demo-data').json()['active']
