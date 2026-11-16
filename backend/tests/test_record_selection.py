import copy
import pytest
from backend.app.record_selection import source_units, resolve_selections
from backend.app.record_quality import check_answers


def fixture():
    segments=[{'segment_id':'a','text':'你的护照是真的吗？我认为是真的。但请核查原件。'}]
    data={'summary':'选择结果','issues':[], 'qa_pairs':[{'id':'p1','question_ids':['u0001'],'answer_ids':['u0002','u0003']}], 'key_facts':[{'label':'q001','qa_ids':['p1']}]}
    return segments,data


def test_original_offsets_and_qualifications_are_preserved():
    segments,data=fixture()
    units=source_units(segments)
    for u in units: assert segments[0]['text'][u['start']:u['end']]==u['text']
    resolved=resolve_selections(data,segments)
    accepted,checks=check_answers(resolved['key_facts'],segments,{'fields':[{'id':'q001','label':'你的护照是真的吗？'}]})
    assert accepted[0]['value']=='我认为是真的。但请核查原件。'
    assert checks[0]['status']=='attention'
    assert accepted[0]['evidence_spans'][0]['quote']==accepted[0]['value']


@pytest.mark.parametrize('ids',[['u9999'],['u0003','u0002'],['u0002','u0002']])
def test_forged_reversed_or_repeated_units_are_rejected(ids):
    segments,data=fixture();data['qa_pairs'][0]['answer_ids']=ids
    with pytest.raises((ValueError,KeyError)): resolve_selections(data,segments)


def test_model_cannot_supply_answer_alongside_selection():
    segments,data=fixture();data['key_facts'][0]['value']='证件真实合法'
    with pytest.raises(ValueError):resolve_selections(data,segments)


def test_cross_clip_pair_retains_both_sources():
    segments=[{'segment_id':'a','text':'你的护照是真的吗？'},{'segment_id':'b','text':'不确定。'}]
    _,data=fixture();data['qa_pairs'][0]['answer_ids']=['u0002']
    result=resolve_selections(data,segments)['key_facts'][0]
    assert result['segment_ids']==['a','b'] and result['value']=='不确定。'


def test_model_question_in_answer_still_blocked():
    segments=[{'segment_id':'a','text':'你的护照是真的吗？不确定。你什么时候来的？'}]
    _,data=fixture()
    resolved=resolve_selections(data,segments)
    accepted,checks=check_answers(resolved['key_facts'],segments,{'fields':[{'id':'q001','label':'你的护照是真的吗？'}]})
    assert not accepted and checks[0]['code']=='question_in_answer'


def test_invalid_selection_does_not_discard_valid_field():
    from backend.app.record_selection import resolve_independently
    segments,data=fixture()
    data['key_facts'].append({'label':'q002','qa_ids':['invented']})
    result=resolve_independently(data,segments)
    accepted,checks=check_answers(result['key_facts'],segments,{'fields':[{'id':'q001','label':'你的护照是真的吗？'},{'id':'q002','label':'其他问题'}]})
    assert [f['label'] for f in accepted]==['q001']
    assert any(c['field_id']=='q002' and c['status']=='blocked' for c in checks)


def test_asr_glued_officer_prompt_is_not_an_answer():
    segments=[{'segment_id':'a','text':'告知书需要再读吗？我看过了，不用再读说一下你的。'}]
    data={'key_facts':[{'label':'q002','qa_ids':['p0001']}]}
    result=resolve_selections(data,segments)
    accepted,checks=check_answers(result['key_facts'],segments,{'fields':[{'id':'q002','label':'告知书需要再读吗？'}]})
    assert not accepted and checks[0]['code']=='question_in_answer'


def test_repeated_answer_is_located_by_selected_offsets():
    segments=[{'segment_id':'a','text':'有没有工作？没有。有没有结婚？没有。'}]
    result=resolve_selections({'key_facts':[{'label':'q001','qa_ids':['p0002']}]},segments)
    accepted,_=check_answers(result['key_facts'],segments,{'fields':[{'id':'q001','label':'有没有结婚？'}]})
    assert accepted[0]['evidence_spans'][0]['start']==segments[0]['text'].rfind('没有。')

from backend.tests.test_records import client, cloud, enable_ai, with_segments, segments, editable

def test_selected_original_reaches_candidates_and_document(client,cloud):
    import httpx,json
    enable_ai(client,'deepseek');row=with_segments(client,template_id='bride')
    text='你的证件是真的假的？我认为是真的。但请核查原件。'
    row=segments(client,row,[editable(row['segments'][0],text=text,speaker="unknown")]).json()
    cloud.respond=lambda request:httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'summary':'选择结果','key_facts':[{'label':'q021','qa_ids':['p0001']}],'issues':[]})}}]})
    response=client.post(f"/api/records/{row['id']}/analyze",json={'confirmed':True,'expected_revision':row['revision']})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['template_snapshot']['values']['q021']=='我认为是真的。但请核查原件。'
    assert result['segments'][0]['text']==text
    assert any(c['field_id']=='q021' for c in result['candidates'])
    assert '但请核查原件' in result['rendered_text']


def test_manually_confirmed_turn_markers_do_not_leak_into_template_answer():
    sources=[{'segment_id':'q','text':'问：你的证件是真的假的？'}, {'segment_id':'a','text':'答：我认为是真的。但请核查原件。'}]
    data=resolve_selections({'key_facts':[{'label':'q021','qa_ids':['p0001']}]},sources)
    accepted,_=check_answers(data['key_facts'],sources,{'fields':[{'id':'q021','label':'你的证件是真的假的？'}]})
    assert accepted[0]['value']=='我认为是真的。但请核查原件。'


def test_confirmed_question_without_question_mark_stops_at_next_officer_turn():
    from backend.app.record_selection import paired_options
    sources=[{'segment_id':'q','speaker':'interviewer','text':'请说一下你的护照情况。'},
             {'segment_id':'a','speaker':'interviewee','text':'我带了护照。但是有效期不确定。'},
             {'segment_id':'next','speaker':'interviewer','text':'现在说一下家庭情况。'}]
    assert len(paired_options(sources))==1
    result=resolve_selections({'key_facts':[{'label':'q001','qa_ids':['p0001']}]},sources)
    accepted,_=check_answers(result['key_facts'],sources,{'fields':[{'id':'q001','label':'护照情况'}]})
    assert accepted[0]['value']=='我带了护照。但是有效期不确定。'


def test_question_mark_in_subject_speech_does_not_create_question():
    from backend.app.record_selection import paired_options
    assert not paired_options([{'segment_id':'a','speaker':'interviewee','text':'你是问哪天？我记不住。'}])


def test_unconfirmed_acoustic_speaker_never_implies_question_or_answer():
    from backend.app.record_selection import paired_options
    assert not paired_options([{'segment_id':'a','text':'[说话人 1] 你叫什么？\n[说话人 2] 金子。'}])


def test_explicit_next_question_without_question_mark_not_swallowed():
    from backend.app.record_selection import paired_options
    sources=[{'segment_id':'a','text':'问：护照情况。\n答：有护照。但日期记不住。\n问：介绍家庭情况。'}]
    result=resolve_selections({'key_facts':[{'label':'q001','qa_ids':['p0001']}]},sources)
    assert result['key_facts'][0]['value']=='有护照。但日期记不住。'


def test_answer_from_officer_is_blocked_even_with_valid_quote():
    sources=[{'segment_id':'s','speaker':'interviewer','text':'护照是真的吗？是真的。'}]
    fact={'label':'q001','value':'是真的。','segment_ids':['s'],'evidence':[{'segment_id':'s','question':'护照是真的吗？','answer':'是真的。'}]}
    accepted,checks=check_answers([fact],sources,{'fields':[{'id':'q001','label':'护照是真的吗？'}]})
    assert not accepted and checks[0]['code']=='role_mismatch'
