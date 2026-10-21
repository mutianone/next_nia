import hashlib
import json
import sqlite3
import subprocess
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.app.database import Base, AuditEvent
from backend.app.people import Person
from backend.app.records import AudioClip
from backend.app.maintenance import snapshot, restore_empty, verify_archive
from backend.app.migration import MigrationItem


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as client: yield client


def person(client, number='P5-1', **extra):
    response = client.post('/api/people', json={'name': '虚构验收人员'+number, 'nationality': '测试国', 'document_number': number, 'source': '合成验收资料', **extra})
    assert response.status_code == 201, response.text
    return response.json()


def fact(**extra):
    return {'label': '陈述事由', 'value': '合成学习事项', 'kind': 'purpose', 'source': '合成材料第 1 页', **extra}


def test_facts_require_evidence_and_review_note_and_detect_stale_revision(client):
    p = person(client); url=f"/api/profiles/{p['id']}/facts"
    assert client.post(url,json=fact(source='')).status_code==422
    assert client.post(url,json=fact(verified=True)).status_code==422
    row=client.post(url,json=fact()).json()
    assert row['verified'] is False and row['source_snapshot']['source']=='合成材料第 1 页'
    payload=fact(verified=True,review_note='人工逐项核验',expected_revision=row['revision'])
    response=client.put(url+'/'+row['id'],json=payload)
    assert response.status_code==200 and response.json()['revision']==2
    assert client.put(url+'/'+row['id'],json=payload).status_code==409
    assert client.get('/api/profiles/'+p['id']).json()['facts'][0]['verified'] is True
    archived=client.put(url+'/'+row['id'],json={**payload,'expected_revision':2,'archived':True})
    assert archived.json()['archived'] is True
    with Session(client.app.state.engine) as session:
        assert '合成学习事项' not in str([x.details for x in session.scalars(select(AuditEvent))])


def test_fact_sources_cannot_cross_people_and_snapshot_survives_source_change(client):
    a,b=person(client),person(client,'P5-2')
    stay=client.post(f"/api/people/{a['id']}/stays",json={'entry_date':'2026-01-02','source':'最初入境记录'}).json()
    payload=fact(source_type='stay',source_id=stay['id'])
    assert client.post(f"/api/profiles/{b['id']}/facts",json=payload).status_code==422
    response=client.post(f"/api/profiles/{a['id']}/facts",json=payload)
    assert response.status_code==201, response.text
    client.put(f"/api/people/{a['id']}/stays/{stay['id']}",json={'entry_date':'2026-01-03','source':'修正后记录'})
    result=client.get('/api/profiles/'+a['id']).json()
    assert result['facts'][0]['source_snapshot']['entry_date']=='2026-01-02'
    assert result['sources'][0]['detail']=='修正后记录'


def test_relations_are_manual_reversible_and_archived_person_readonly(client):
    a,b=person(client),person(client,'P5-2')
    url=f"/api/profiles/{a['id']}/relations"
    payload={'target_id':b['id'],'label':'已登记亲属','source':'合成关系材料'}
    assert client.post(url,json={**payload,'target_id':a['id']}).status_code==422
    row=client.post(url,json=payload).json()
    related=client.get('/api/profiles/'+b['id']).json()['relations'][0]
    assert related['other']['id']==a['id'] and related['verified'] is False
    result=client.put(url+'/'+row['id'],json={**payload,'expected_revision':1,'verified':True,'review_note':'核对合成证明'})
    assert result.status_code==200
    client.post('/api/people/'+a['id']+'/archive',json={'archived':True})
    assert client.post(url,json=payload).status_code==409
    assert client.post(f"/api/profiles/{a['id']}/facts",json=fact()).status_code==409
    assert client.get('/api/profiles/'+a['id']).status_code==200


def test_profile_connects_records_documents_stays_without_automatically_verifying(client):
    a=person(client)
    record=client.post('/api/records',json={'title':'合成询问笔录','person_id':a['id'],'scenario':'study'}).json()
    assert record['id']
    response=client.get('/api/profiles/'+a['id']).json()
    assert response['facts']==[]
    assert any(row['type']=='record' for row in response['sources'])
    assert any(row['kind']=='record' for row in response['timeline'])


def test_analytics_date_boundaries_archive_scope_zero_days_and_csv_safety(client):
    a=person(client,purpose='=SUM(A1)',nationality='测试国')
    b=person(client,'P5-2')
    with Session(client.app.state.engine) as session:
        session.get(Person,a['id']).created_at=datetime(2026,1,1,16,0,tzinfo=timezone.utc) # Jan 2 in Shanghai
        session.get(Person,b['id']).created_at=datetime(2026,1,1,15,59,tzinfo=timezone.utc)
        session.commit()
    for p in [a,b]:
        assert client.post(f"/api/people/{p['id']}/stays",json={'entry_date':'2026-01-02','exit_date':'2026-01-04','source':'合成事实'}).status_code==201
    client.post('/api/people/'+b['id']+'/archive',json={'archived':True})
    report=client.get('/api/analytics?start=2026-01-02&end=2026-01-04').json()
    assert report['stock']['people']==1 and report['period']['new_people']==1
    assert report['period']['entries']==report['period']['exits']==1
    assert report['trend'][1]=={'date':'2026-01-03','entries':0,'exits':0}
    assert client.get('/api/analytics?start=2026-01-02&end=2026-01-04&include_archived=true').json()['period']['entries']==2
    exported=client.get('/api/analytics/export?start=2026-01-02&end=2026-01-04')
    assert "'=SUM(A1)" in exported.text
    assert client.get('/api/analytics?start=2026-02-01&end=2026-01-01').status_code==422
    assert client.get('/api/analytics?start=2020-01-01&end=2026-01-01').status_code==422


def batch():
    return {'source_key':'legacy-test','items':[{'legacy_id':'visa:1','person':{'name':'虚构迁入甲','nationality':'测试国','document_number':'MIG1','source':'旧库人工校验'},'stays':[{'entry_date':'2026-01-02','source':'核验后入境事实'}],'original':{'expiryDate':'2020-01-01','leave_date':'2020-02-01'}}]}


def test_migration_preview_transaction_replay_changed_batch_and_originals(client):
    data=batch(); bad=batch(); bad['items'][0]['person']['nationality']=''
    assert client.post('/api/migration/preview',json=bad).json()['errors']
    assert client.get('/api/people').json()['total']==0
    preview=client.post('/api/migration/preview',json=data).json(); assert preview['can_import']
    assert client.post('/api/migration/commit',json={**data,'preview_hash':preview['preview_hash'],'confirmed':False}).status_code==422
    changed=batch();changed['items'][0]['person']['name']='虚构新姓名'
    assert client.post('/api/migration/commit',json={**changed,'preview_hash':preview['preview_hash'],'confirmed':True}).status_code==409
    payload={**data,'preview_hash':preview['preview_hash'],'confirmed':True}
    assert client.post('/api/migration/commit',json=payload).json()['imported']==1
    assert client.post('/api/migration/commit',json=payload).json()=={'imported':0,'skipped':1}
    assert client.post('/api/migration/preview',json=changed).json()['errors']
    p=client.get('/api/people').json()['items'][0]
    details=client.get('/api/people/'+p['id']).json()
    assert details['stays'][0]['permitted_until'] is None and details['stays'][0]['exit_date'] is None
    with Session(client.app.state.engine) as session:
        assert session.scalar(select(MigrationItem)).original['expiryDate']=='2020-01-01'


def test_migration_rechecks_duplicate_on_commit_and_rolls_back_entire_batch(client):
    data=batch();data['items'].append({'legacy_id':'visa:2','person':{**data['items'][0]['person'],'document_number':'MIG2'}})
    preview=client.post('/api/migration/preview',json=data).json()
    person(client,'MIG2')
    assert client.post('/api/migration/commit',json={**data,'preview_hash':preview['preview_hash'],'confirmed':True}).status_code==409
    assert client.get('/api/people').json()['total']==1


def test_snapshot_round_trip_retains_original_bytes_facts_relationships_and_audit(client,tmp_path):
    a,b=person(client),person(client,'P5-2')
    client.post('/api/profiles/'+a['id']+'/facts',json=fact())
    client.post('/api/profiles/'+a['id']+'/relations',json={'target_id':b['id'],'label':'测试关系','source':'测试依据'})
    record=client.post('/api/records',json={'title':'虚构录音测试','person_id':a['id']}).json()
    original=b'ID3'+bytes(range(256))*20
    with Session(client.app.state.engine) as session:
        session.add(AudioClip(record_id=record['id'],filename='test.mp3',format='mp3',mime_type='audio/mpeg',size_bytes=len(original),sha256=hashlib.sha256(original).hexdigest(),raw_audio=original));session.commit()
    source=tmp_path/'backup.zip';manifest=snapshot(client.app.state.engine,source)
    assert source.stat().st_mode&0o777==0o600
    assert verify_archive(source)==manifest
    destination=create_engine('sqlite:///'+str(tmp_path/'restored.sqlite3'))
    restore_empty(destination,source)
    with Session(destination) as session:
        assert session.scalar(select(func.count()).select_from(Person))==2
        assert session.scalar(select(AudioClip)).raw_audio==original
        assert len(session.scalars(select(AuditEvent)).all())>0
    with pytest.raises(ValueError,match='非空'): restore_empty(destination,source)
    destination.dispose()


def test_archive_rejects_tampering_unknown_entries_and_does_not_include_credentials(client,tmp_path):
    archive=tmp_path/'safe.zip';snapshot(client.app.state.engine,archive)
    with zipfile.ZipFile(archive) as z: contents={name:z.read(name) for name in z.namelist()}
    assert not any('settings' in name for name in contents)
    corrupt=tmp_path/'corrupt.zip'
    with zipfile.ZipFile(corrupt,'w') as z:
        for name,body in contents.items(): z.writestr(name,body+b'\n' if name=='people.jsonl' else body)
    with pytest.raises(ValueError,match='校验失败'): verify_archive(corrupt)
    with zipfile.ZipFile(corrupt,'a') as z: z.writestr('../unexpected','x')
    with pytest.raises(ValueError,match='不匹配'): verify_archive(corrupt)


def test_download_backup_and_maintenance_endpoint(client,tmp_path):
    person(client)
    response=client.post('/api/maintenance/backup')
    assert response.status_code==200 and response.headers['content-type']=='application/zip'
    archive=tmp_path/'download.zip';archive.write_bytes(response.content)
    assert verify_archive(archive)['tables']['people']['rows']==1
    info=client.get('/api/maintenance').json()
    assert info['counts']['people']==1 and info['events'][0]['action']=='maintenance.exported'


def test_legacy_cli_is_readonly_preserves_ambiguous_fields_and_requires_review(tmp_path):
    source=tmp_path/'old.sqlite3'
    with sqlite3.connect(source) as c:
        c.execute('CREATE TABLE qian_zheng_data (id INTEGER, name TEXT, idCard TEXT, expiryDate TEXT, leave_date TEXT)')
        c.execute("INSERT INTO qian_zheng_data VALUES (1,'虚构原档案','OLD1','2025-01-02','2025-01-03')")
    before=hashlib.sha256(source.read_bytes()).hexdigest();output=tmp_path/'staging.json'
    result=subprocess.run([__import__('sys').executable,'scripts/maintenance.py','legacy-stage','--source',str(source),'--output',str(output)],capture_output=True)
    assert result.returncode==0,result.stderr
    data=json.loads(output.read_text());item=data['items'][0]
    assert item['person']['nationality']=='' and item['stays']==[]
    assert item['original']['leave_date']=='2025-01-03'
    assert before==hashlib.sha256(source.read_bytes()).hexdigest()
    assert output.stat().st_mode&0o777==0o600


def test_cli_restore_requires_new_directory_and_verifies_without_starting_service(client,tmp_path):
    person(client);archive=tmp_path/'data.zip';snapshot(client.app.state.engine,archive)
    output=tmp_path/'fresh';args=[__import__('sys').executable,'scripts/maintenance.py','restore',str(archive),'--new-data-dir',str(output)]
    first=subprocess.run(args,capture_output=True);assert first.returncode==0,first.stderr
    assert (output/'test.sqlite3').exists()
    assert subprocess.run(args,capture_output=True).returncode==1


def test_static_build_serve_mode_and_health_version(tmp_path,monkeypatch):
    monkeypatch.setenv('NEXT_NIA_SERVE_DIST','1')
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/').status_code==200
        assert client.get('/api/health').json()['version']=='0.5.0'
        assert client.get('/api/missing').status_code==404
