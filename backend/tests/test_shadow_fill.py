"""Stage-three acceptance using synthetic local material; no cloud calls."""
from copy import deepcopy
from importlib import import_module
from io import BytesIO
from zipfile import ZipFile
import pytest
from sqlalchemy import select,func,create_engine,inspect
from sqlalchemy.orm import Session
from backend.tests.test_record_capture import client
from backend.tests.test_qa_drafts import seed,review_all,get,post
from backend.app import records as r
from backend.app.record_qa_state_machine import QAPair
from backend.app.record_qa_drafts import QADraft
from backend.app.record_fill_candidates import FillBatch,FillCandidate,FillReview
from backend.app.record_templates import CASE_TEMPLATES
from backend.app.record_word import export_original,ROOT

DEFAULT={'id':'synthetic','version':'1','fields':[{'id':'q_name','label':'你叫什么名字？','question_aliases':['请问你的姓名']},{'id':'q_address','label':'你住在哪里？'}]}

def setup(client,texts=None,template=None,confirm=True):
    row,_=seed(client,**({'texts':texts,'speakers':tuple(i%2 for i in range(len(texts)))} if texts else {}))
    with r.write_session(client.app.state.engine) as s:s.get(r.RecordSession,row['id']).template_snapshot={'name':'合成测试',**deepcopy(template or DEFAULT)}
    row=get(client,row,'')
    review_all(client,row);d=post(client,row,'qa-drafts/rebuild').json()
    if confirm:
        res=post(client,row,f'qa-drafts/{d["id"]}/confirm',draft_revision=d['revision']);assert res.status_code==200,res.text;d=res.json()
    return row,d

def preview(client,row,d,**kw):return post(client,row,'fill-preview',draft_id=d['id'],draft_revision=d['revision'],**kw)
def review(client,row,c,**kw):return post(client,row,f'fill-candidates/{c["candidate_id"]}/review',candidate_revision=c['revision'],action=kw.pop('action','ACCEPTED'),**kw)

def test_two_pairs_idempotence_provenance_and_formal_unchanged(client):
    row,d=setup(client);before=get(client,row,'');result=preview(client,row,d);assert result.status_code==200,result.text
    b=result.json();assert [c['answer_text'] for c in b['items']]==['张某。','我住在依安县某小区。']
    assert [c['template_field_id'] for c in b['items']]==['q_name','q_address']
    assert all(len(c['source_refs'])==2 and c['source_digest'] for c in b['items'])
    assert preview(client,row,d).json()['id']==b['id']
    c=b['items'][0];accepted=review(client,row,c);assert accepted.status_code==200,accepted.text
    assert review(client,row,c).status_code==409
    assert preview(client,row,d).json()['items'][0]['review']['status']=='ACCEPTED'
    after=get(client,row,'');assert after['segments']==before['segments'] and after['template_snapshot']==before['template_snapshot'] and after['revision']==before['revision']
    with Session(client.app.state.engine) as s:
        assert s.scalar(select(func.count(FillBatch.id)))==1 and s.scalar(select(func.count(FillCandidate.id)))==2
        assert s.scalar(select(FillReview)).actor_id=='synthetic-user'

def test_long_answer_alias_and_human_edit_history(client):
    answer='我叫金子，不是金枝。'+('后来我在家里等了一会儿。'*200)+'具体时间记不清。'
    row,d=setup(client,('请问你的姓名',answer));c=preview(client,row,d).json()['items'][0]
    assert c['match_method']=='ALIAS' and c['answer_text']==answer
    changed=review(client,row,c,action='MODIFIED',value='人工核对后的文字',note='合成测试');assert changed.status_code==200,changed.text
    c2=changed.json();assert c2['answer_text']==answer and c2['review']['value_source']=='HUMAN'
    again=review(client,row,c2,action='REJECTED');assert len(again.json()['reviews'])==2

def test_unmatched_manual_binding_and_free_qa(client):
    row,d=setup(client,('还有什么需要说明？','没有了。'))
    c=preview(client,row,d).json()['items'][0];assert c['candidate_status']=='UNMATCHED'
    assert review(client,row,c).status_code==409
    assert review(client,row,c,template_field_id='q_name').status_code==200
    with r.write_session(client.app.state.engine) as s:
        rec=s.get(r.RecordSession,row['id']);t=deepcopy(rec.template_snapshot);t['fields'].append({'id':'free','label':'自由问答','kind':'free_qa','allow_multiple':True});rec.template_snapshot=t
    row=get(client,row,'')
    b=preview(client,row,d).json();assert b['items'][0]['match_method']=='FREE_QA'
    assert b['items'][0]['question_text']=='还有什么需要说明？'

@pytest.mark.parametrize('kind',['draft','source','template','locked'])
def test_stale_or_unconfirmed_protected(client,kind):
    row,d=setup(client,confirm=kind!='draft')
    if kind=='draft':assert preview(client,row,d).status_code==409;return
    b=preview(client,row,d).json()
    with r.write_session(client.app.state.engine) as s:
        rec=s.get(r.RecordSession,row['id'])
        if kind=='source':
            seg=s.scalar(select(r.RecordSegment).where(r.RecordSegment.record_id==row['id']));seg.text+='人工校正'
        else:
            t=deepcopy(rec.template_snapshot)
            if kind=='template':t['version']='2'
            else:t.update(locked_fields=['q_name'],values={'q_name':'已确认文字'})
            rec.template_snapshot=t
    assert get(client,row,'fill-comparison')['batch']['stale']
    assert review(client,row,b['items'][0]).status_code==409
    if kind=='locked':
        row=get(client,row,'')
        c=preview(client,row,d).json()['items'][0];assert 'PROTECTED_FIELD' in c['problems'];assert review(client,row,c).status_code==409

@pytest.mark.parametrize('exclusive',[False,True])
def test_shared_source_and_exclusivity(client,exclusive):
    fields=[{'id':f'q_{n}','label':'你叫什么名字？','allow_shared_source':True,**({'exclusive_group':'identity'} if exclusive else {})} for n in ('one','two')]
    row,d=setup(client,('你叫什么名字？','金子。'),{'id':'test','version':'1','fields':fields})
    b=preview(client,row,d).json();assert len(b['items'])==2
    assert review(client,row,b['items'][0]).status_code==(409 if exclusive else 200)
    if exclusive:
        assert review(client,row,b['items'][1],action='REJECTED').status_code==200
        assert review(client,row,b['items'][0]).status_code==200

@pytest.mark.parametrize('corruption',['truncated','duplicate','timestamp'])
def test_reject_invalid_qa_provenance(client,corruption):
    row,d=setup(client)
    with r.write_session(client.app.state.engine) as s:
        q=s.scalar(select(QAPair).where(QAPair.draft_id==d['id']).order_by(QAPair.position));p=deepcopy(q.data)
        if corruption=='truncated':p['answer']=p['answer'][:-1]
        elif corruption=='duplicate':p['sources'].append(p['sources'][0])
        else:p['sources'][0]['start_ms']=999999
        q.data=p
    assert preview(client,row,d).status_code==409

@pytest.mark.parametrize('template',CASE_TEMPLATES,ids=lambda t:t['id'])
def test_all_three_original_templates_matching_content_and_word_compatibility(client,template):
    t=deepcopy(template);questions=[f for f in t['fields'] if f['id'].startswith('q')][:2]
    answers=['听明白了。','已经阅读，权利义务清楚。']
    row,d=setup(client,tuple(v for q,a in zip(questions,answers) for v in (q['label'],a)),t)
    result=preview(client,row,d);assert result.status_code==200,result.text;b=result.json()
    assert [c['template_field_id'] for c in b['items']]==[q['id'] for q in questions]
    assert [c['answer_text'] for c in b['items']]==answers
    # Isolated render test: copy only, not a formal database write or endpoint switch.
    render=deepcopy(t);render['values']={q['id']:a for q,a in zip(questions,answers)}
    package=export_original(render,'合成测试')
    with ZipFile(BytesIO(package)) as generated,ZipFile(ROOT/t['word_asset']) as original:
        text=generated.read('word/document.xml').decode()
        assert all(a in text for a in answers)
        for name in original.namelist():
            if name!='word/document.xml':assert generated.read(name)==original.read(name)
    assert get(client,row,'')['template_snapshot']==t

def test_additive_migration_is_repeatable():
    engine=create_engine('sqlite://');m=import_module('backend.migrations.20261115_shadow_fill');m.upgrade(engine);m.upgrade(engine)
    assert set(inspect(engine).get_table_names())=={'record_fill_batches','record_fill_candidates','record_fill_reviews'}

def test_historical_without_structured_draft(client):
    row,_=seed(client)
    assert get(client,row,'fill-candidates')['batch'] is None
    assert post(client,row,'fill-preview',draft_id='missing',draft_revision=1).status_code==409

def test_follow_up_and_long_continuation_remain_separate(client):
    row,_=seed(client,('你当时在哪里？','我在家里。','后来有人敲门。','具体几点？','八点左右。'),(0,1,1,0,1))
    with r.write_session(client.app.state.engine) as s:
        s.get(r.RecordSession,row['id']).template_snapshot={'id':'test','version':'1','name':'测试','fields':[{'id':'q_where','label':'你当时在哪里？'},{'id':'q_time','label':'具体几点？'}]}
    row=get(client,row,'');data=get(client,row,'speaker-mappings')
    roles=['OFFICER','INTERVIEWEE','INTERVIEWEE','OFFICER','INTERVIEWEE'];intents=['QUESTION','ANSWER','CONTINUATION','FOLLOW_UP','ANSWER']
    for sentence,role,intent in zip(data['sentences'],roles,intents):
        response=post(client,row,f'speaker-sentences/{sentence["id"]}/review',sentence_revision=0,parts=[dict(start=0,end=len(sentence['text']),role=role,intent=intent)])
        assert response.status_code==200,response.text
    d=post(client,row,'qa-drafts/rebuild').json();confirmed=post(client,row,f'qa-drafts/{d["id"]}/confirm',draft_revision=d['revision']);assert confirmed.status_code==200,confirmed.text
    b=preview(client,row,confirmed.json());assert b.status_code==200,b.text
    items=b.json()['items'];assert items[0]['answer_text']=='我在家里。\n后来有人敲门。'
    assert items[1]['question_text']=='具体几点？' and items[1]['answer_text']=='八点左右。' and items[1]['follow_up_of']

def test_duplicate_target_needs_manual_resolution(client):
    row,d=setup(client,('你叫什么名字？','金子。','你叫什么名字？','也叫阿金。'))
    b=preview(client,row,d).json();assert all('MULTIPLE_ANSWERS_FOR_FIELD' in c['problems'] for c in b['items'])
    assert review(client,row,b['items'][0]).status_code==409
    assert review(client,row,b['items'][1],action='REJECTED').status_code==200
    assert review(client,row,b['items'][0]).status_code==200

def test_open_issue_and_cross_record_access(client):
    from backend.app.record_qa_adhesion import QAReviewIssue
    row,d=setup(client);other,_=setup(client)
    assert preview(client,other,d).status_code==409
    with r.write_session(client.app.state.engine) as s:s.add(QAReviewIssue(id='synthetic-issue',record_id=row['id'],draft_id=d['id'],fingerprint='synthetic',data={'type':'UNKNOWN_ROLE'}))
    assert preview(client,row,d).status_code==409

def test_semantic_adapter_mock_only_and_no_fact_rewrite(client,monkeypatch):
    import json
    from backend.app import records_ai
    from backend.tests.test_records import enable_ai
    enable_ai(client)
    row,d=setup(client,('您怎么称呼？','金子。'))
    def provider(url,key,**kwargs):
        questions=json.loads(kwargs['json']['messages'][1]['content'])['questions']
        assert 'answer' not in questions[0]
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'matches':[{'qa_pair_id':questions[0]['qa_pair_id'],'field_ids':['q_name']}]})}}]}
    monkeypatch.setattr(records_ai,'bounded_post',provider)
    assert preview(client,row,d,use_semantic=True).status_code==400
    res=preview(client,row,d,use_semantic=True,confirmed=True);assert res.status_code==200,res.text
    c=res.json()['items'][0];assert c['match_method']=='SEMANTIC' and c['answer_text']=='金子。'

def test_audio_deletion_purges_shadow_dependents(client):
    from backend.app.record_template_fill import purge_draft
    row,d=setup(client);c=preview(client,row,d).json()['items'][0];assert review(client,row,c).status_code==200
    with r.write_session(client.app.state.engine) as s:purge_draft(s,d['id'])
    with Session(client.app.state.engine) as s:
        for model in (FillBatch,FillCandidate,FillReview):assert s.scalar(select(func.count(model.id)))==0
        assert s.get(QADraft,d['id']) is not None

def test_new_batch_preserves_accepted_shadow_version(client):
    row,d=setup(client);b=preview(client,row,d).json();c=b['items'][0]
    assert review(client,row,c).status_code==200
    newer=preview(client,row,d,bindings={c['qa_pair_id']:[]}).json()
    assert newer['id']!=b['id']
    old=get(client,row,f'fill-comparison?batch_id={b["id"]}')['batch']
    assert old['items'][0]['review']['status']=='ACCEPTED' and old['items'][0]['answer_text']==c['answer_text']

def test_semantic_source_race_is_rejected(client,monkeypatch):
    from backend.app import record_fill_api
    from backend.tests.test_records import enable_ai
    enable_ai(client);row,d=setup(client)
    def racing(pairs,template,settings):
        with r.write_session(client.app.state.engine) as s:
            rec=s.get(r.RecordSession,row['id']);t=deepcopy(rec.template_snapshot);t['version']='race';rec.template_snapshot=t
        return {},{}
    monkeypatch.setattr(record_fill_api,'semantic_suggestions',racing)
    assert preview(client,row,d,use_semantic=True,confirmed=True).status_code==409
    with Session(client.app.state.engine) as s:assert s.scalar(select(func.count(FillBatch.id)))==0


def test_old_batch_rechecks_mixed_turn_review_before_fill(client):
    from backend.app.record_qa_adhesion import QAReviewIssue
    row, draft = setup(client, ('你叫什么名字？', '名字吗我叫李某。'), confirm=False)
    issue = next(x for x in get(client, row, 'qa-review-issues')['items']
                 if x['draft_id'] == draft['id'] and x['type'] == 'MIXED_SPEAKER_SUSPECTED')
    assert post(client, row, f"qa-review-issues/{issue['id']}/resolve",
                issue_revision=1, note='已回听逐字核实').status_code == 200
    draft = post(client, row, f"qa-drafts/{draft['id']}/confirm", draft_revision=draft['revision']).json()
    batch = preview(client, row, draft).json()
    # A pre-upgrade confirmed draft/batch lacks the explicit boundary review.
    with r.write_session(client.app.state.engine) as session:
        session.delete(session.get(QAReviewIssue, issue['id']))
    assert preview(client, row, draft).status_code == 409
    comparison = get(client, row, 'fill-comparison')['batch']
    assert comparison['stale'] and '粘连' in comparison['reason']
    assert review(client, row, batch['items'][0]).status_code == 409
