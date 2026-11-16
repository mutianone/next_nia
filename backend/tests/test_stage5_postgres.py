"""Opt-in isolated PostgreSQL suite. Never reads application DB configuration.
STAGE5_TEST_PG_URL must name a localhost crj_stage5_test* database; each test owns
one UUID schema. No database is dropped. Missing URL = explicitly skipped.
"""
import os
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from backend.app import records as r
from backend.app.config import SettingsStore
from backend.tests import test_fill_apply as core
from backend.tests.test_qa_drafts import get,url

@pytest.fixture
def client(tmp_path):
    value=os.environ.get('STAGE5_TEST_PG_URL')
    if not value:pytest.skip('No explicitly isolated PostgreSQL test instance configured')
    u=make_url(value)
    if u.get_backend_name()!='postgresql' or u.host not in {'127.0.0.1','localhost','::1'} or not (u.database or '').startswith('crj_stage5_test'):
        pytest.fail('Only explicit localhost crj_stage5_test databases are allowed')
    schema='stage5_'+uuid4().hex
    admin=create_engine(u,hide_parameters=True)
    with admin.begin() as c:c.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_engine(u,connect_args={'options':f'-csearch_path={schema}'},hide_parameters=True)
    try:
        r.Base.metadata.create_all(engine)
        app=FastAPI();app.state.engine=engine;app.state.store=SettingsStore(tmp_path);app.state.store.load()
        @app.middleware('http')
        async def actor(request,call_next):
            request.state.user={'id':'synthetic-pg','role':'business'};return await call_next(request)
        app.include_router(r.router)
        with TestClient(app) as c:yield c
    finally:
        engine.dispose()
        with admin.begin() as c:c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()

@pytest.mark.parametrize('same_key',[True,False])
def test_concurrent_applications(client,same_key):core.test_concurrent_requests_are_serialized(client,same_key)
def test_response_loss_and_restore(client):core.test_atomic_apply_idempotent_retry_and_restore(client)
def test_mid_transaction_failure(client,monkeypatch):core.test_transaction_failure_rolls_back_all_writes(client,monkeypatch)
def test_commit_failure(client,monkeypatch):core.test_commit_failure_leaves_no_application_or_revision(client,monkeypatch)
def test_field_revision(client):core.test_version_conflicts(client,'expected_field_revisions')
def test_feature_off(client):core.test_disabled_and_separate_confirmation(client)

@pytest.mark.parametrize('operation',['apply','restore'])
def test_human_edit_race(client,operation):
    row,b=core.ready(client);body=core.request_body(client,row,b)
    if operation=='restore':
        assert core.apply(client,row,body).status_code==200
        item=get(client,row,'fill-apply/history')['items'][0];body=item['restore_preview'];body.pop('differences')
        body={**body,'reason':'synthetic race','confirmed':True,'idempotency_key':str(uuid4())}
        path=url(row,f'fill-apply/{item["application_id"]}/restore')
    else:
        # A formal baseline is needed by the existing manual-edit endpoint.
        with r.write_session(client.app.state.engine) as s:
            rec=s.get(r.RecordSession,row['id']);t=dict(rec.template_snapshot);t['generated']=True;t['source_hash']=r.segment_hash(r.ordered_segments(s,rec.id));rec.template_snapshot=t;r.version(s,rec,'synthetic_baseline')
        # The template changed; make a fresh reviewed batch, never weaken staleness.
        from backend.tests.test_shadow_fill import preview,review
        from backend.app.record_qa_drafts import QADraft
        from sqlalchemy.orm import Session
        with Session(client.app.state.engine) as s:d=s.get(QADraft,b['draft_id']);draft={'id':d.id,'revision':d.revision}
        row=get(client,row,'');b=preview(client,row,draft).json()
        for c in b['items']:assert review(client,row,c).status_code==200
        body=core.request_body(client,row,b);path=url(row,'fill-apply')
    rev=body['expected_record_revision']
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(client.post,path,json=body)
        h=pool.submit(client.patch,url(row,'template-values'),json={'expected_revision':rev,'values':{'q_name':'人工核对'}})
        statuses=sorted([a.result().status_code,h.result().status_code])
    assert statuses==[200,409]


def test_different_candidates_concurrent(client):
    row,b=core.ready(client)
    bodies=[core.request_body(client,row,b,[c['candidate_id']]) for c in b['items'][:2]]
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda body:core.apply(client,row,body),bodies))
    assert sorted(x.status_code for x in responses)==[200,409]
    assert len(get(client,row,'')['template_snapshot']['values'])==1
