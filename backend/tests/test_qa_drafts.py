"""Stage-two API acceptance: temporary DB, synthetic text, no cloud requests."""
from importlib import import_module
import pytest
from sqlalchemy import select,func,create_engine,inspect
from sqlalchemy.orm import Session
from backend.tests.test_record_capture import client
from backend.tests.test_records import create
from backend.tests.test_asr_results import output
from backend.app import records as r,record_asr_results as ar
from backend.app.record_qa_drafts import QADraft
from backend.app.record_qa_state_machine import QAPair
from backend.app.record_speaker_roles import SpeakerRoleMapping


def seed(client,texts=('你叫什么名字？','张某。','你住在哪里？','我住在依安县某小区。'),speakers=(0,1,0,1)):
    row=create(client)
    with r.write_session(client.app.state.engine) as s:
        run=ar.save_result(s,row['id'],'audio:synthetic',output('one',speakers,texts))
        record=s.get(r.RecordSession,row['id']);ar.project(s,run,record,None,None,0)
        run_id=run.id
    return row,run_id


def url(row,suffix):return f'/api/records/{row["id"]}/{suffix}'
def get(client,row,suffix):
    response=client.get(url(row,suffix));assert response.status_code==200,response.text;return response.json()
def post(client,row,suffix,**data):return client.post(url(row,suffix),json={'expected_revision':row['revision'],**data})

def review_all(client,row):
    data=get(client,row,'speaker-mappings')
    for i,s in enumerate(data['sentences']):
        p=dict(start=0,end=len(s['text']),role='OFFICER' if i%2==0 else 'INTERVIEWEE',intent='QUESTION' if i%2==0 else 'ANSWER')
        response=post(client,row,f'speaker-sentences/{s["id"]}/review',sentence_revision=s['revision'],parts=[p])
        assert response.status_code==200,response.text


@pytest.mark.parametrize('intents', [('CONTINUATION','QUESTION','CONTINUATION'), ('QUESTION','CONTINUATION','CONTINUATION')])
def test_multisentence_officer_turn_reaches_fill_preview_with_all_sources(client, intents):
    lines = (('先核对一下时间。', '你几点到的？', '我问的是上午。') if intents[0]=='CONTINUATION' else
             ('你几点到的？', '我问的是上午。', '请你仔细想想。'))
    answer = '我记不清，可能是十点。'
    row,_ = seed(client, (*lines, answer), (0,0,0,1))
    material = get(client,row,'speaker-mappings')['sentences']
    assert [s['same_voice_as_previous'] for s in material] == [False,True,True,False]
    for s,intent in zip(material, (*intents,'ANSWER')):
        role = 'INTERVIEWEE' if intent=='ANSWER' else 'OFFICER'
        res = post(client,row,f'speaker-sentences/{s["id"]}/review',sentence_revision=0,
                   parts=[dict(start=0,end=len(s['text']),role=role,intent=intent)])
        assert res.status_code==200,res.text
    draft = post(client,row,'qa-drafts/rebuild').json()
    question,reply = draft['turns']
    assert question['text']=='\n'.join(lines) and question['intent']=='QUESTION'
    pair, = draft['pairs']
    assert pair['state']=='QA_COMPLETED' and pair['answer']==reply['text']==answer
    assert [s['sentence_id'] for s in pair['sources']]==[s['id'] for s in material]
    assert not get(client,row,'qa-review-issues')['items']
    assert post(client,row,'qa-drafts/rebuild').json()['id']==draft['id']
    confirmed=post(client,row,f'qa-drafts/{draft["id"]}/confirm',draft_revision=draft['revision']).json()
    res=post(client,row,'fill-preview',draft_id=draft['id'],draft_revision=confirmed['revision'])
    assert res.status_code==200,res.text
    item, = res.json()['items']
    assert item['answer_text']==answer and item['question_text']==question['text']
    assert item['source_refs']==pair['sources']


def test_two_pairs_independent_projection_idempotent_and_confirmed_protection(client):
    row,run_id=seed(client);before=get(client,row,'')
    review_all(client,row)
    response=post(client,row,'qa-drafts/rebuild');assert response.status_code==200,response.text
    d=response.json();assert len(d['pairs'])==2 and d['pairs'][0]['answer']=='张某。'
    assert d['pairs'][1]['answer']=='我住在依安县某小区。'
    assert all(p['sources'] for p in d['pairs'])
    assert post(client,row,'qa-drafts/rebuild').json()['id']==d['id']
    assert post(client,row,f'qa-drafts/{d["id"]}/confirm',draft_revision=d['revision']).status_code==200
    with Session(client.app.state.engine) as s:
        assert s.scalar(select(func.count(QAPair.id)))==2
        assert s.scalar(select(func.count(QADraft.id)))==1
    # A different recognition version is explicitly selected, never automatically substituted.
    with r.write_session(client.app.state.engine) as s:
        new=ar.save_result(s,row['id'],'audio:synthetic',output('two',(0,1),('你叫什么名字？','李某。')));new_id=new.id
    d2=post(client,row,'qa-drafts/rebuild',run_ids=[new_id]).json()
    assert d2['id']!=d['id'] and d2['status']=='REVIEW_REQUIRED'
    old=next(x for x in get(client,row,'qa-drafts')['items'] if x['id']==d['id'])
    assert old['status']=='CONFIRMED' and old['pairs'][0]['answer']=='张某。'
    after=get(client,row,'');assert after['segments']==before['segments'] and after['template_snapshot']==before['template_snapshot']


def test_unknown_format_does_not_confirm_roles(client):
    row,_=seed(client,('问：你叫什么名字？答：张某。',),(None,))
    d=post(client,row,'qa-drafts/rebuild').json()
    assert d['turns'][0]['role']=='UNKNOWN'
    issues=get(client,row,'qa-review-issues')['items'];assert any(i['type']=='UNKNOWN_ROLE' for i in issues)
    for issue in issues:
        assert post(client,row,f'qa-review-issues/{issue["id"]}/resolve',issue_revision=issue['revision'],note='核对中').status_code==200
    assert post(client,row,f'qa-drafts/{d["id"]}/confirm',draft_revision=d['revision']).status_code==409
    assert post(client,row,'qa-drafts/rebuild').json()['id']==d['id']
    assert len(get(client,row,'qa-review-issues')['items'])==len(issues)


def test_mapping_actor_scope_stale_revision_and_sentence_override(client):
    row,run_id=seed(client)
    data=dict(recognition_run_id=run_id,channel_id=0,speaker_id=0,business_role='OFFICER',mapping_revision=0)
    assert post(client,row,'speaker-mappings',**data).status_code==200
    assert post(client,row,'speaker-mappings',**data).status_code==409
    maps=get(client,row,'speaker-mappings')
    assert maps['history'][0]['actor']=='synthetic-user'
    first=maps['sentences'][0]
    assert post(client,row,f'speaker-sentences/{first["id"]}/review',sentence_revision=0,parts=[dict(start=0,end=len(first['text']),role='OTHER',intent='OTHER')]).status_code==200
    assert get(client,row,'speaker-mappings')['sentences'][0]['parts'][0]['role']=='OTHER'
    other=create(client)
    assert post(client,other,'speaker-mappings',**data).status_code==409


def test_draft_confirmation_rejects_changed_source_and_optimistic_lock(client):
    row,_=seed(client);review_all(client,row);d=post(client,row,'qa-drafts/rebuild').json()
    assert post(client,row,f'qa-drafts/{d["id"]}/confirm',draft_revision=99).status_code==409
    with r.write_session(client.app.state.engine) as s:
        segment=s.scalar(select(r.RecordSegment).where(r.RecordSegment.record_id==row['id']));segment.text='人工已修改。'
    assert post(client,row,f'qa-drafts/{d["id"]}/confirm',draft_revision=1).status_code==409
    new=post(client,row,'qa-drafts/rebuild').json()
    assert new['id']!=d['id']
    assert any(i['type']=='CONFIRMED_CONTENT_CONFLICT' for i in get(client,row,'qa-review-issues')['items'])


def test_history_empty_and_archived_writes_rejected(client):
    row=create(client)
    assert get(client,row,'speaker-turns')['items']==[]
    assert post(client,row,'qa-drafts/rebuild').status_code==409
    row,_=seed(client)
    with r.write_session(client.app.state.engine) as s:s.get(r.RecordSession,row['id']).status='archived'
    assert post(client,row,'qa-drafts/rebuild').status_code==409


def test_versions_not_mixed_and_deletion_clears_derived_sources(client):
    row,run_id=seed(client)
    with r.write_session(client.app.state.engine) as s:
        new=ar.save_result(s,row['id'],'audio:synthetic',output('two'));new_id=new.id
    assert post(client,row,'qa-drafts/rebuild',run_ids=[run_id,new_id]).status_code==409
    post(client,row,'qa-drafts/rebuild')
    with r.write_session(client.app.state.engine) as s:ar.purge_audio(s,row['id'],None)
    assert get(client,row,'qa-drafts')['items']==[] and get(client,row,'speaker-mappings')['items']==[]


def test_migration_additive_idempotent(tmp_path):
    engine=create_engine(f'sqlite:///{tmp_path}/old.sqlite')
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE legacy (value TEXT)');conn.exec_driver_sql("INSERT INTO legacy VALUES ('keep')")
    migration=import_module('backend.migrations.20261115_qa_drafts');migration.upgrade(engine);migration.upgrade(engine)
    assert 'record_qa_draft_pairs' in inspect(engine).get_table_names()
    with engine.connect() as conn:assert conn.exec_driver_sql('SELECT value FROM legacy').scalar()=='keep'
    engine.dispose()


def test_manual_pairing_multiple_questions_and_cross_batch_confirmation(client):
    row,run_id=seed(client,('你叫什么名字？','你住在哪里？','张某。','住依安县。'),(0,0,1,1))
    data=get(client,row,'speaker-mappings')
    for i,s in enumerate(data['sentences']):
        assert post(client,row,f'speaker-sentences/{s["id"]}/review',sentence_revision=0,parts=[dict(start=0,end=len(s['text']),role='OFFICER' if i<2 else 'INTERVIEWEE',intent='QUESTION' if i<2 else 'ANSWER')]).status_code==200
    # Independent answers stay separate; cross-batch continuation needs explicit review.
    with r.write_session(client.app.state.engine) as s:
        new=ar.save_result(s,row['id'],'audio:other',output('third',(1,),('补充回答。',)));new.applied=True;new_id=new.id
    data=get(client,row,'speaker-mappings');last=next(x for x in data['sentences'] if x['recognition_run_id']==new_id)
    post(client,row,f'speaker-sentences/{last["id"]}/review',sentence_revision=0,parts=[dict(start=0,end=len(last['text']),role='INTERVIEWEE',intent='ANSWER')])
    d=post(client,row,'qa-drafts/rebuild',run_ids=[run_id,new_id]).json();ts=d['turns']
    assert len(ts)==5
    entries=[dict(question_turn_ids=[ts[0]['id']],answer_turn_ids=[ts[2]['id']],other_turn_ids=[]),dict(question_turn_ids=[ts[1]['id']],answer_turn_ids=[ts[3]['id'],ts[4]['id']],other_turn_ids=[])]
    path=f'qa-drafts/{d["id"]}/pairs/review'
    assert post(client,row,path,draft_revision=d['revision'],pairs=entries).status_code==409
    entries[1]['confirm_cross_run']=True
    response=post(client,row,path,draft_revision=d['revision'],pairs=entries)
    assert response.status_code==200,response.text
    revised=response.json();assert len(revised['pairs'])==2 and revised['id']!=d['id']
    assert post(client,row,path,draft_revision=d['revision'],pairs=entries).json()['id']==revised['id']
    duplicate=entries+[entries[0]]
    assert post(client,row,path,draft_revision=d['revision'],pairs=duplicate).status_code==409


def test_read_only_role_cannot_mutate_without_main_middleware(client):
    # Production middleware rejects viewers; service guard also requires an actor.
    from backend.app.record_qa_draft_api import actor
    from types import SimpleNamespace
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:actor(SimpleNamespace(state=SimpleNamespace(user={'id':'v','role':'viewer'})))
    assert e.value.status_code==403


def test_previous_stage_backup_can_be_restored_without_new_tables(client,tmp_path):
    import json,zipfile
    from backend.app.maintenance import snapshot,verify_archive,restore_empty
    full=tmp_path/'new.zip';old=tmp_path/'old.zip'
    snapshot(client.app.state.engine,full)
    names={'record_speaker_role_mappings','record_sentence_role_reviews','record_speaker_turns','record_qa_draft_pairs','record_qa_review_issues','record_qa_drafts'}
    with zipfile.ZipFile(full) as src,zipfile.ZipFile(old,'w') as dst:
        manifest=json.loads(src.read('manifest.json'))
        for name in names:manifest['tables'].pop(name)
        dst.writestr('manifest.json',json.dumps(manifest))
        for name in src.namelist():
            if name!='manifest.json' and name.removesuffix('.jsonl') not in names:dst.writestr(name,src.read(name))
    assert verify_archive(old)
    engine=create_engine(f'sqlite:///{tmp_path}/restored.sqlite');restore_empty(engine,old)
    assert 'record_qa_drafts' in inspect(engine).get_table_names();engine.dispose()


def test_resolved_issue_keeps_original_resolution(client):
    row,_=seed(client);post(client,row,'qa-drafts/rebuild')
    issue=get(client,row,'qa-review-issues')['items'][0];path=f'qa-review-issues/{issue["id"]}/resolve'
    assert post(client,row,path,issue_revision=1,note='原处理结果').status_code==200
    assert post(client,row,path,issue_revision=2,note='不能覆盖原结果').status_code==409
    saved=next(x for x in get(client,row,'qa-review-issues')['items'] if x['id']==issue['id'])
    assert saved['resolution']['note']=='原处理结果'


def test_rebuild_rechecks_legacy_glued_answer_without_mutating_previous_draft(client):
    from backend.app.record_qa_adhesion import QAReviewIssue
    row,_ = seed(client, ('确定没偷渡吗。', '偷渡吗我确定'), (0, 1))
    review_all(client, row)
    original = post(client, row, 'qa-drafts/rebuild').json()
    # Simulate a persisted pre-boundary-check draft for the same transcript.
    with r.write_session(client.app.state.engine) as session:
        old = session.get(QADraft, original['id'])
        old.input_hash = old.source_hash
        for issue in session.scalars(select(QAReviewIssue).where(QAReviewIssue.draft_id == old.id)):
            session.delete(issue)
    rebuilt = post(client, row, 'qa-drafts/rebuild').json()
    assert rebuilt['id'] != original['id'] and rebuilt['status'] == 'REVIEW_REQUIRED'
    assert rebuilt['pairs'][0]['answer'] == original['pairs'][0]['answer'] == '偷渡吗我确定'
    assert post(client, row, 'qa-drafts/rebuild').json()['id'] == rebuilt['id']
    assert post(client, row, f"qa-drafts/{rebuilt['id']}/confirm", draft_revision=rebuilt['revision']).status_code == 409
