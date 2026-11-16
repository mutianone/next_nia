"""Migration/feature rollout drill exclusively on pytest temporary databases."""
from importlib import import_module
from pathlib import Path
import sqlite3
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from backend.tests.test_record_capture import client
from backend.tests.test_records import create
from backend.tests.test_fill_apply_word import word_fixture
from backend.tests.test_fill_apply import request_body,apply
from backend.tests.test_qa_drafts import get,url
from backend.app import records as r
from backend.app.record_templates import CASE_TEMPLATES


def test_temporary_deploy_backup_migrate_apply_restore_disable(client,tmp_path):
    engine=client.app.state.engine;legacy=create(client,title='合成历史笔录')
    modules=[import_module('backend.migrations.'+name) for name in ['20261115_asr_results','20261115_qa_drafts','20261115_shadow_fill','20261116_fill_apply']]
    tables=[modules[0].RecognitionRun,modules[0].RecognitionSentence]+[t for m in modules[1:] for t in m.TABLES]
    # Construct a pre-stage-one schema only in this fixture, before any QA exists.
    for model in reversed(tables):model.__table__.drop(engine,checkfirst=True)
    db=Path(engine.url.database);assert db.is_relative_to(tmp_path)
    backup=tmp_path/'before-migrations.sqlite'
    with sqlite3.connect(db) as original,sqlite3.connect(backup) as target:original.backup(target)
    for m in modules:m.upgrade(engine);m.upgrade(engine)
    assert all(t.__tablename__ in inspect(engine).get_table_names() for t in tables)
    assert get(client,legacy,'')['title']=='合成历史笔录'
    assert client.get('/api/records/_features/structured-fill').json()['record_structured_fill_apply_enabled'] is False
    app=FastAPI();app.state.engine=engine;app.state.store=client.app.state.store
    @app.middleware('http')
    async def actor(request,call_next):
        request.state.user={'id':'synthetic-rollout-admin','role':'admin'};return await call_next(request)
    app.include_router(r.router)
    with TestClient(app) as admin:
        feature='/api/records/_features/structured-fill'
        assert admin.put(feature,json={'expected_revision':0,'record_structured_fill_apply_enabled':True}).status_code==200
        row,b,_,_,_=word_fixture(client,CASE_TEMPLATES[0])
        assert apply(client,row,request_body(client,row,b)).status_code==200
        assert client.get(url(row,'export.docx')).status_code==200
        item=get(client,row,'fill-apply/history')['items'][0];p=item['restore_preview'];p.pop('differences')
        assert client.post(url(row,f'fill-apply/{item["application_id"]}/restore'),json={**p,'confirmed':True,'idempotency_key':str(uuid4()),'reason':'测试环境回退演练'}).status_code==200
        assert client.get(url(row,'export.docx')).status_code==200
        assert admin.put(feature,json={'expected_revision':1,'record_structured_fill_apply_enabled':False}).status_code==200
        assert client.post(url(row,'fill-apply/preview'),json={'fill_batch_id':b['id'],'candidate_ids':[x['candidate_id'] for x in b['items']]}).status_code==409
    # Existing manual formal editing and export still work after disabling.
    current=get(client,row,'')
    assert client.patch(url(row,'template-values'),json={'expected_revision':current['revision'],'values':{'q001':'人工核对内容'}}).status_code==200
    assert client.get(url(row,'export.docx')).status_code==200
    with sqlite3.connect(backup) as saved:
        assert saved.execute('select count(*) from record_sessions').fetchone()[0]==1
