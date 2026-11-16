"""Temporary databases, confirmed synthetic QA, no external requests."""
from copy import deepcopy
from uuid import uuid4
from sqlalchemy import select,func
from sqlalchemy.orm import Session
import pytest
from backend.tests.test_record_capture import client
from backend.tests.test_shadow_fill import setup,preview,review
from backend.tests.test_qa_drafts import get,url
from backend.app import records as r,record_fill_apply as service
from backend.app.record_fill_api import Review


def ready(client,template=None,texts=None,enable=True):
    row,d=setup(client,template=template,texts=texts)
    with r.write_session(client.app.state.engine) as s:
        rec=s.get(r.RecordSession,row['id']);rec.template_id=rec.template_snapshot['id']
        if enable:service.setting(s,True).enabled=True # synthetic test environment only
    row=get(client,row,'');b=preview(client,row,d).json()
    for c in b['items']:
        response=review(client,row,c);assert response.status_code==200,response.text
    return row,b


def request_preview(client,row,b,ids=None):return client.post(url(row,'fill-apply/preview'),json={'fill_batch_id':b['id'],'candidate_ids':ids or [c['candidate_id'] for c in b['items']]})
def request_body(client,row,b,ids=None):
    response=request_preview(client,row,b,ids);assert response.status_code==200,response.text
    p=response.json();p.pop('differences');return {**p,'idempotency_key':str(uuid4()),'confirmed':True}
def apply(client,row,body):return client.post(url(row,'fill-apply'),json=body)


def test_atomic_apply_idempotent_retry_and_restore(client):
    row,b=ready(client);before=get(client,row,'');body=request_body(client,row,b)
    result=apply(client,row,body);assert result.status_code==200,result.text
    app=result.json();after=get(client,row,'')
    assert after['template_snapshot']['values']=={'q_name':'张某。','q_address':'我住在依安县某小区。'}
    assert after['segments']==before['segments'] and after['revision']==before['revision']+1
    assert apply(client,row,body).json()==app # response-loss retry uses original revisions
    changed={**body,'idempotency_key':str(uuid4())};assert apply(client,row,changed).status_code==409
    history=get(client,row,'fill-apply/history');p=history['items'][0]['restore_preview'];assert p
    p.pop('differences');restore={**p,'idempotency_key':str(uuid4()),'confirmed':True,'reason':'合成回滚测试'}
    path=url(row,f'fill-apply/{app["application_id"]}/restore')
    result=client.post(path,json=restore);assert result.status_code==200,result.text
    assert client.post(path,json=restore).json()==result.json()
    restored=get(client,row,'');assert restored['template_snapshot']['values']=={} and restored['revision']==after['revision']+1
    assert len(get(client,row,'fill-apply/history')['items'])==2


def test_disabled_and_separate_confirmation(client):
    row,b=ready(client,enable=False)
    assert request_preview(client,row,b).status_code==409
    with r.write_session(client.app.state.engine) as s:service.setting(s,True).enabled=True
    body=request_body(client,row,b);body['confirmed']=False
    assert apply(client,row,body).status_code==400
    assert not get(client,row,'')['template_snapshot'].get('generated')

@pytest.mark.parametrize('key',['expected_record_revision','expected_template_revision','expected_field_revisions','expected_candidate_revisions'])
def test_version_conflicts(client,key):
    row,b=ready(client);body=request_body(client,row,b)
    if key=='expected_template_revision':body[key]='0'*64
    elif isinstance(body[key],dict):body[key]={k:v+1 for k,v in body[key].items()}
    else:body[key]+=1
    assert apply(client,row,body).status_code==409
    assert not get(client,row,'')['template_snapshot'].get('generated')


def test_transaction_failure_rolls_back_all_writes(client,monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    row,b=ready(client);body=request_body(client,row,b);before=get(client,row,'')
    original=r.audit
    def fail(session,action,**kw):
        if action=='structured_fill_application':raise SQLAlchemyError('synthetic failure after version flush')
        return original(session,action,**kw)
    monkeypatch.setattr(r,'audit',fail)
    assert apply(client,row,body).status_code==503
    after=get(client,row,'');assert after['revision']==before['revision'] and after['template_snapshot']==before['template_snapshot']
    with Session(client.app.state.engine) as s:assert s.scalar(select(func.count(service.FillApplication.id)))==0
    monkeypatch.setattr(r,'audit',original);assert apply(client,row,body).status_code==200


def test_restore_rejects_manual_edit_even_if_changed_back(client):
    row,b=ready(client);result=apply(client,row,request_body(client,row,b)).json()
    with r.write_session(client.app.state.engine) as s:
        rec=s.get(r.RecordSession,row['id']);original=deepcopy(rec.template_snapshot)
        t=deepcopy(original);t['values']['q_name']='后续人工值';rec.template_snapshot=t;r.version(s,rec,'synthetic_manual')
        rec.template_snapshot=original;r.version(s,rec,'synthetic_manual_back')
    item=get(client,row,'fill-apply/history')['items'][0]
    assert item['restore_preview'] is None and '后续修改' in item['restore_error']


def test_no_permission_to_enable(client):
    response=client.put('/api/records/_features/structured-fill',json={'record_structured_fill_apply_enabled':True,'expected_revision':0})
    assert response.status_code==403
    assert client.get('/api/records/_features/structured-fill').json()['record_structured_fill_apply_enabled'] is False

@pytest.mark.parametrize('mutation',['unconfirmed','unreviewed','source','template','locked','state'])
def test_invalid_material_never_partially_applied(client,mutation):
    from backend.app.record_qa_drafts import QADraft
    from backend.app.record_fill_candidates import FillBatch,FillReview
    row,b=ready(client);body=request_body(client,row,b)
    with r.write_session(client.app.state.engine) as s:
        rec=s.get(r.RecordSession,row['id'])
        if mutation=='unconfirmed':s.get(QADraft,s.get(FillBatch,b['id']).draft_id).status='PROVISIONAL'
        elif mutation=='unreviewed':
            rev=s.scalar(select(FillReview).where(FillReview.candidate_id==b['items'][1]['candidate_id']));rev.data={**rev.data,'status':'REJECTED'}
        elif mutation=='source':s.scalar(select(r.RecordSegment).where(r.RecordSegment.record_id==row['id'])).text+='补充'
        elif mutation=='state':rec.status='archived'
        else:
            t=deepcopy(rec.template_snapshot)
            if mutation=='locked':t['locked_fields']=['q_address']
            else:t['version']='changed'
            rec.template_snapshot=t
    before=get(client,row,'');assert apply(client,row,body).status_code==409
    after=get(client,row,'');assert after['template_snapshot']==before['template_snapshot']
    with Session(client.app.state.engine) as s:assert s.scalar(select(func.count(service.FillApplication.id)))==0

@pytest.mark.parametrize('same_key',[True,False])
def test_concurrent_requests_are_serialized(client,same_key):
    from concurrent.futures import ThreadPoolExecutor
    row,b=ready(client);body=request_body(client,row,b);second={**body,'idempotency_key':body['idempotency_key'] if same_key else str(uuid4())}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda p:apply(client,row,p),[body,second]))
    assert sorted(x.status_code for x in results)==([200,200] if same_key else [200,409])
    with Session(client.app.state.engine) as s:assert s.scalar(select(func.count(service.FillApplication.id)))==1


def test_commit_failure_leaves_no_application_or_revision(client):
    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError
    row,b=ready(client);body=request_body(client,row,b);before=get(client,row,'')
    def fail(conn):raise SQLAlchemyError('synthetic commit failure')
    event.listen(client.app.state.engine,'commit',fail)
    try:assert apply(client,row,body).status_code==503
    finally:event.remove(client.app.state.engine,'commit',fail)
    after=get(client,row,'');assert after['revision']==before['revision'] and after['template_snapshot']==before['template_snapshot']
    assert apply(client,row,body).status_code==200


def test_admin_flag_revision_and_audit(client):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.database import AuditEvent
    app=FastAPI();app.state.engine=client.app.state.engine
    @app.middleware('http')
    async def admin(request,call_next):
        request.state.user={'id':'synthetic-admin','role':'admin'};return await call_next(request)
    app.include_router(r.router)
    with TestClient(app) as admin:
        path='/api/records/_features/structured-fill'
        assert admin.put(path,json={'record_structured_fill_apply_enabled':True,'expected_revision':0}).status_code==200
        assert admin.put(path,json={'record_structured_fill_apply_enabled':False,'expected_revision':0}).status_code==409
        assert admin.put(path,json={'record_structured_fill_apply_enabled':False,'expected_revision':1}).status_code==200
    with Session(app.state.engine) as s:
        audit=list(s.scalars(select(AuditEvent).where(AuditEvent.action=='records.structured_fill_feature_changed')))
        assert len(audit)==2 and all(a.details['actor_id']=='synthetic-admin' for a in audit)
        assert service.enabled(s) is False


def test_migration_repeatable_and_disabled_by_default():
    from importlib import import_module
    from sqlalchemy import create_engine,inspect
    engine=create_engine('sqlite://');m=import_module('backend.migrations.20261116_fill_apply');m.upgrade(engine);m.upgrade(engine)
    assert set(inspect(engine).get_table_names())=={'record_fill_apply_settings','record_fill_apply_batches','record_fill_apply_items'}
    with Session(engine) as s:assert not service.enabled(s)


def test_free_qa_preserves_question_and_cannot_invent_word_region(client):
    template={'id':'free_test','version':'1','name':'合成测试','fields':[{'id':'free','label':'自由问答','kind':'free_qa','allow_multiple':True}]}
    row,b=ready(client,template=template,texts=('还有其他情况吗？','暂时没有了。'))
    assert apply(client,row,request_body(client,row,b)).status_code==200
    assert get(client,row,'')['template_snapshot']['values']['free']=='问：还有其他情况吗？\n答：暂时没有了。'


def test_selected_subset_does_not_drop_unmatched_candidates(client):
    from backend.app.record_fill_candidates import FillCandidate
    row,d=setup(client,texts=('你叫什么名字？','金子。','还有什么？','我不知道。'))
    with r.write_session(client.app.state.engine) as s:s.get(r.RecordSession,row['id']).template_id='synthetic';service.setting(s,True).enabled=True
    row=get(client,row,'');b=preview(client,row,d).json();assert review(client,row,b['items'][0]).status_code==200
    assert apply(client,row,request_body(client,row,b,[b['items'][0]['candidate_id']])).status_code==200
    with Session(client.app.state.engine) as s:assert s.get(FillCandidate,b['items'][1]['candidate_id']).data['answer_text']=='我不知道。'


def test_restore_preserves_later_edits_to_unrelated_fields(client):
    row,b=ready(client);body=request_body(client,row,b,[b['items'][0]['candidate_id']]);assert apply(client,row,body).status_code==200
    current=get(client,row,'')
    changed=client.patch(url(row,'template-values'),json={'expected_revision':current['revision'],'values':{'q_address':'后来人工核对的地址'}});assert changed.status_code==200
    item=get(client,row,'fill-apply/history')['items'][0];p=item['restore_preview'];assert p;p.pop('differences')
    result=client.post(url(row,f'fill-apply/{item["application_id"]}/restore'),json={**p,'confirmed':True,'reason':'只恢复姓名','idempotency_key':str(uuid4())})
    assert result.status_code==200,result.text
    t=get(client,row,'')['template_snapshot'];assert t['values']=={'q_address':'后来人工核对的地址'} and t['locked_fields']==['q_address']


def test_candidate_raw_text_corruption_rejected(client):
    from backend.app.record_fill_candidates import FillCandidate
    row,b=ready(client);body=request_body(client,row,b)
    with r.write_session(client.app.state.engine) as s:
        c=s.get(FillCandidate,b['items'][0]['candidate_id']);c.data={**c.data,'answer_text':'非原答'}
    assert apply(client,row,body).status_code==409


def test_mutually_exclusive_targets_rechecked_even_with_bad_legacy_reviews(client):
    from backend.app.record_fill_candidates import FillCandidate,FillReview
    template={'id':'synthetic','version':'1','name':'合成','fields':[{'id':'one','label':'你叫什么名字？','allow_shared_source':True,'exclusive_group':'identity'},{'id':'two','label':'你叫什么名字？','allow_shared_source':True,'exclusive_group':'identity'}]}
    row,d=setup(client,template=template,texts=('你叫什么名字？','金子。'))
    with r.write_session(client.app.state.engine) as s:s.get(r.RecordSession,row['id']).template_id='synthetic';service.setting(s,True).enabled=True
    row=get(client,row,'');b=preview(client,row,d).json()
    with r.write_session(client.app.state.engine) as s:
        for data in b['items']:
            c=s.get(FillCandidate,data['candidate_id']);c.revision=2
            s.add(FillReview(id=str(uuid4()),candidate_id=c.id,revision=2,data={'status':'ACCEPTED','template_field_id':data['template_field_id'],'value':'金子。','value_source':'QA_ORIGINAL'},actor_id='synthetic',created_at='2026-11-16'))
    response=request_preview(client,row,b);assert response.status_code==409 and '互斥' in response.text


def test_arbitrary_formal_values_are_not_accepted_from_client(client):
    row,b=ready(client);body=request_body(client,row,b);body['values']={'q_name':'绕过审核'}
    assert apply(client,row,body).status_code==422


def test_backup_restores_history_but_never_enables_feature(client,tmp_path):
    import zipfile
    from sqlalchemy import create_engine
    from backend.app.maintenance import snapshot,restore_empty
    row,b=ready(client);body=request_body(client,row,b)
    assert apply(client,row,body).status_code==200
    archive=tmp_path/'backup.zip';snapshot(client.app.state.engine,archive)
    with zipfile.ZipFile(archive) as z:
        assert 'record_fill_apply_settings.jsonl' not in z.namelist()
        assert 'record_fill_apply_batches.jsonl' in z.namelist()
    target=create_engine('sqlite:///'+str(tmp_path/'restored.sqlite'))
    restore_empty(target,archive)
    with Session(target) as s:
        assert not service.enabled(s)
        assert s.scalar(select(func.count(service.FillApplication.id)))==1
    target.dispose()
