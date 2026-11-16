import json
from pathlib import Path
import pytest
from backend.app.record_quality import check_answers
from backend.app.record_templates import CASE_TEMPLATES

CASES=json.loads((Path(__file__).parent/'fixtures/record_acceptance/quality_cases.json').read_text())

@pytest.mark.parametrize('case',CASES,ids=[c['id'] for c in CASES])
def test_fixed_quality_acceptance(case):
    t=next(t for t in CASE_TEMPLATES if t['id']==case['template_id'])
    answer=case.get('quote',case['answer'])
    text=case['question']+case['answer']
    fact={'label':case['field'],'value':case.get('value',answer),'segment_ids':['s1'],'evidence':[] if case.get('no_evidence') else [{'segment_id':'s1','question':case['question'],'answer':answer}]}
    accepted,checks=check_answers([fact],[{'segment_id':'s1','text':text}],t)
    assert bool(accepted)==case['expected']
    if accepted:
        span=accepted[0]['evidence_spans'][0]
        assert text[span['start']:span['end']]==answer
    else:assert checks[0]['status']=='blocked'


def test_long_multiple_recordings_preserve_exact_evidence():
    t=next(t for t in CASE_TEMPLATES if t['id']=='bride')
    q='你的家庭情况？'
    segments=[{'segment_id':'first','text':'无关背景。'*1500+q+'我父亲叫张三。'}, {'segment_id':'second','text':q+'我母亲叫李丽。'}]
    fact={'label':'q004','value':'我父亲叫张三。\n我母亲叫李丽。','segment_ids':['first','second'],'evidence':[{'segment_id':'first','question':q,'answer':'我父亲叫张三。'},{'segment_id':'second','question':q,'answer':'我母亲叫李丽。'}]}
    accepted,_=check_answers([fact],segments,t)
    assert len(accepted[0]['evidence_spans'])==2
    fact['evidence'][1]['segment_id']='missing'
    assert not check_answers([fact],segments,t)[0]


def test_retries_are_bounded_and_evidence_failures_not_retried(monkeypatch):
    from backend.app import record_cloud as cloud
    from backend.app.records_ai import RecordAIError
    sleeps=[];calls=[]
    monkeypatch.setattr(cloud,'reserve',lambda:None)
    monkeypatch.setattr(cloud.time,'sleep',sleeps.append)
    def transient():
        calls.append(1)
        if len(calls)<3:raise RecordAIError(429,'rate limit')
        return 'ok'
    assert cloud.retry(transient)=='ok'
    assert sleeps==[65,130] and len(calls)==3
    sleeps.clear()
    with pytest.raises(RecordAIError):cloud.retry(lambda:(_ for _ in ()).throw(RecordAIError(502,'invalid evidence')))
    assert sleeps==[]


def test_question_and_answer_across_adjacent_clips():
    t=next(t for t in CASE_TEMPLATES if t['id']=='bride')
    sources=[{'segment_id':'first','text':'你的家庭情况？'},{'segment_id':'second','text':'我父亲叫张三。'}]
    f={'label':'q004','value':'我父亲叫张三。','segment_ids':['first','second'],'evidence':[{'question_segment_id':'first','segment_id':'second','question':'你的家庭情况？','answer':'我父亲叫张三。'}]}
    assert check_answers([f],sources,t)[0]
    assert not check_answers([f],sources[::-1],t)[0]

from backend.tests.test_records import client, cloud, enable_ai, with_segments, segments, editable, analysis_response

def test_blocked_answer_never_reaches_document_or_candidates(client,cloud):
    enable_ai(client,'deepseek');row=with_segments(client,template_id='bride')
    question='你在中国居留有没有合法证件？';answer='我带了护照，但有效期需要核查。'
    row=segments(client,row,[editable(row['segments'][0],text=question+answer,speaker='unknown')]).json()
    sid=row['segments'][0]['id']
    cloud.respond=lambda request:analysis_response(sid,summary='未经证实的合法居留结论',key_facts=[{'label':'q014','value':'我有合法居留证件。','segment_ids':[sid],
        'evidence':[{'segment_id':sid,'question':question,'answer':answer}]}])
    result=client.post(f"/api/records/{row['id']}/analyze",json={'confirmed':True,'expected_revision':row['revision']})
    assert result.status_code==200,result.text
    row=result.json()
    assert 'q014' not in row['template_snapshot']['values']
    assert '我有合法居留证件' not in row['rendered_text']
    assert not any(c['field_id']=='q014' for c in row['candidates'])
    assert row['template_snapshot']['quality_checks'][0]['code']=='rewritten_answer'
    assert row['segments'][0]['text']==question+answer
    assert '未经证实的合法居留结论' not in row['analysis']['summary']
