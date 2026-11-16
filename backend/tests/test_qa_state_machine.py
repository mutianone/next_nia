import pytest
from backend.app.record_qa_state_machine import pair_turns
from backend.app.record_qa_adhesion import detect

def turns(spec):
    return [dict(id=str(i),role=r,intent=intent,suggested_intent='UNKNOWN',text=text,status='CONFIRMED',sources=[dict(sentence_id=str(i),recognition_run_id='run',origin_key='audio:x',channel_id=0,start=0,end=len(text),start_ms=i*1000,end_ms=i*1000+900,source_intervals=[])]) for i,(r,intent,text) in enumerate(spec)]
Q=('OFFICER','QUESTION','你叫什么名字？');A=('INTERVIEWEE','ANSWER','张某。')

def test_two_distinct_pairs():
    ts=turns([Q,A,('OFFICER','QUESTION','你住在哪里？'),('INTERVIEWEE','ANSWER','我住在依安县某小区。')])
    ps=pair_turns(ts)
    assert len(ps)==2 and all(p['state']=='QA_COMPLETED' for p in ps)
    assert ps[0]['answer_turn_ids']==['1'] and ps[1]['answer_turn_ids']==['3']
    assert not detect(ts,ps)

def test_multiple_questions_not_arbitrarily_assigned():
    ps=pair_turns(turns([Q,Q,A]));assert len(ps)==3
    assert not any(p['answer_turn_ids'] for p in ps)
    assert ps[-1]['candidate_question_ids']==['qa-1','qa-2']

def test_long_answer_and_followup():
    ps=pair_turns(turns([Q,A,('INTERVIEWEE','CONTINUATION','后来有人敲门。'),('OFFICER','FOLLOW_UP','具体几点？'),A]))
    assert len(ps)==2 and ps[0]['answer_turn_ids']==['1','2'] and ps[1]['follow_up_of']=='qa-1'
    assert ps[1]['question_turn_ids']==['3']

def test_counterquestion_is_not_answer_or_officer():
    ps=pair_turns(turns([Q,('INTERVIEWEE','CLARIFICATION','你说的是哪个王某？')]))
    assert not ps[0]['answer_turn_ids'] and ps[0]['clarification_turn_ids']==['1']

@pytest.mark.parametrize('spec',[[('UNKNOWN','UNKNOWN','问：你叫什么？答：张某。')],[('OFFICER','INSTRUCTION','请阅读权利义务告知书。')],[Q,('OTHER','OTHER','我来说'),A]])
def test_unknown_instructions_and_interruptions(spec):
    ps=pair_turns(turns(spec));assert not any(p['state']=='QA_COMPLETED' for p in ps)

def test_run_switch_no_automatic_pair():
    ts=turns([Q,A]);ts[1]['sources'][0]['recognition_run_id']='other'
    assert len(pair_turns(ts))==2

def test_detector_duplicates_cross_roles_versions_and_timeline():
    ts=turns([Q,A]);ts.append({**ts[1],'id':'copy'})
    assert 'DUPLICATE_SOURCE' in {x['type'] for x in detect(ts,pair_turns(ts)).values()}
    ts=turns([Q,A]);ts[1]['sources'][0]['start_ms']=500
    assert 'TIMELINE_CONFLICT' in {x['type'] for x in detect(ts,pair_turns(ts)).values()}
    ts[1]['sources'][0]['recognition_run_id']='two'
    assert 'RECOGNITION_VERSION_CONFLICT' in {x['type'] for x in detect(ts,pair_turns(ts)).values()}


def test_confirmed_cross_role_and_mixed_text_are_findings_not_automatic_splits():
    ts=turns([('INTERVIEWEE','ANSWER','张某。你住在哪里？')]);original=ts[0]['text']
    ts[0]['sources'].append({**ts[0]['sources'][0],'sentence_id':'other','role':'OFFICER'})
    findings=detect(ts,pair_turns(ts))
    assert {'CROSS_ROLE_MERGE','MIXED_SPEAKER_SUSPECTED'}<={x['type'] for x in findings.values()}
    assert ts[0]['text']==original


def test_unanswered_question_and_long_pause_preserve_qa():
    assert pair_turns(turns([Q]))[0]['reasons']==['UNANSWERED_QUESTION']
    ts=turns([Q,A,('INTERVIEWEE','CONTINUATION','后来才出去。')]);ts[-1]['sources'][0].update(start_ms=100000,end_ms=101000)
    assert pair_turns(ts)[0]['answer_turn_ids']==['1','2']


@pytest.mark.parametrize('question,answer', [
    ('确定没偷渡吗。', '偷渡吗我确定，但日期我记不住。'),
    ('确定没偷渡吗？', '吗。 我不确定。'),
    ('你的证件号是多少？', '多少我记不住。'),
    ('这些情况都属实，对不对？', '对不对我确定。'),
])
def test_question_tail_in_confirmed_answer_triggers_review(question, answer):
    ts = turns([('OFFICER', 'QUESTION', question), ('INTERVIEWEE', 'ANSWER', answer)])
    findings = detect(ts, pair_turns(ts))
    assert any(i['type'] == 'MIXED_SPEAKER_SUSPECTED' and i['turn_ids'] == ['1']
               for i in findings.values())
    assert ts[1]['text'] == answer


def test_two_pairs_in_one_asr_sentence_preserve_every_character():
    from backend.app.record_qa_boundaries import split_candidates
    expected = ['你的出入境证件号是123吗？', '是的。', '确定没偷渡吗。', '我不确定，但我记得大概时间。']
    text = ''.join(expected)
    parts, origins = split_candidates([{'id': 's', 'text': text}])
    assert [p['text'] for p in parts] == expected
    assert set(origins.values()) == {'s'}


@pytest.mark.parametrize('question,answer', [
    ('你有护照？', '护照我带了。'),
    ('你确定没偷渡吗？', '我不确定，但我记得大概时间。'),
    ('你听见他说什么？', '他问我“确定没偷渡吗？”我当时没有回答。'),
    ('你还记得多少？', '多少年前的事我不记得了。'),
    ('上述内容对不对？', '不对，我没有这么说。'),
])
def test_repetition_or_negation_alone_is_not_a_role_change(question, answer):
    ts = turns([('OFFICER', 'QUESTION', question), ('INTERVIEWEE', 'ANSWER', answer)])
    assert not detect(ts, pair_turns(ts))
