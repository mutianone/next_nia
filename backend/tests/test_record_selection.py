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


def test_two_short_replies_keep_independent_exact_source_spans():
    from types import SimpleNamespace
    from backend.app.record_candidates import pair_qa, rule_facts

    questions = ['你的出入境证件号是xxxxx吗？', '确定没偷渡吗。']
    text = f'问：{questions[0]}答：是的。 问：{questions[1]}答：偷渡吗我不确定，但我记得当时带了证件。'
    template = {'fields': [{'id': f'q{i}', 'label': q} for i, q in enumerate(questions)]}
    sources = [{'segment_id': 's', 'text': text}]
    selected = resolve_selections({'key_facts': [
        {'label': 'q0', 'qa_ids': ['p0001']}, {'label': 'q1', 'qa_ids': ['p0002']}]}, sources)
    accepted, _ = check_answers(selected['key_facts'], sources, template)
    pairs = pair_qa([SimpleNamespace(id='s', text=text, speaker='unknown')], template)
    expected = ['是的。', '我不确定，但我记得当时带了证件。']
    assert [f['value'] for f in rule_facts(pairs)] == [f['value'] for f in accepted] == expected
    for pair, value, fact in zip(pairs, expected, accepted):
        for span in (pair['sources'][1], fact['evidence_spans'][0]):
            assert text[span['start']:span['end']] == value


def test_plain_asr_with_period_after_second_question_keeps_both_pairs():
    sources = [{'segment_id': 's', 'text': '你的出入境证件号是xxxxx吗？是的。确定没偷渡吗。我确定。'}]
    facts = [{'label': 'q1', 'qa_ids': ['p0001']}, {'label': 'q2', 'qa_ids': ['p0002']}]
    result = resolve_selections({'key_facts': facts}, sources)
    assert [f['value'] for f in result['key_facts']] == ['是的。', '我确定。']


@pytest.mark.parametrize(('gap', 'answer'), [('', '偷渡吗我确定。'), ('没有。', '我确定。')])
def test_raw_model_evidence_cannot_include_a_question_tail_or_skip_a_denial(gap, answer):
    q = '确定没偷渡吗？'
    sources = [{'segment_id': 's', 'text': q + gap + answer}]
    fact = {'label': 'q1', 'value': answer, 'segment_ids': ['s'],
            'evidence': [{'segment_id': 's', 'question': q, 'answer': answer}]}
    accepted, checks = check_answers([fact], sources, {'fields': [{'id': 'q1', 'label': q}]})
    assert not accepted and checks[0]['status'] == 'blocked'


def test_exact_rule_cannot_pair_across_unconfirmed_speech():
    from types import SimpleNamespace
    from backend.app.record_candidates import pair_qa, rule_facts
    turns = [('interviewer', '确定没偷渡吗？'), ('unknown', '确定什么？'), ('interviewee', '我确定。')]
    sources = [SimpleNamespace(id=str(i), text=text, speaker=role) for i, (role, text) in enumerate(turns)]
    pairs = pair_qa(sources, {'fields': [{'id': 'q1', 'label': turns[0][1]}]})
    assert not rule_facts(pairs)


def test_reported_dialogue_is_not_an_officer_question_with_a_short_reply():
    question = '你带护照了吗？'
    sources = [{'segment_id': 's', 'text': f'他问我：“{question}”我回答过。'}]
    fact = {'label': 'q1', 'value': '我回答过。', 'segment_ids': ['s'],
            'evidence': [{'segment_id': 's', 'question': question, 'answer': '我回答过。'}]}
    accepted, checks = check_answers([fact], sources, {'fields': [{'id': 'q1', 'label': question}]})
    assert not accepted and checks[0]['code'] == 'role_mismatch'


@pytest.mark.parametrize('text', ['你知道为什么吗？对的。', '会求数列吗？定不会。', '遇见不同的人吗？北方的人有吗？没都是南方的。'])
def test_unconfirmed_asr_susceptible_to_polarity_or_turn_errors_requires_review(text):
    sources = [{'segment_id': 's', 'text': text, 'recognition_run_id': 'run', 'speaker': 'unknown'}]
    result = resolve_selections({'key_facts': [{'label': 'q1', 'qa_ids': ['p0001']}]}, sources)
    fact = result['key_facts'][0]
    accepted, checks = check_answers([fact], sources, {'fields': [{'id': 'q1', 'label': fact['evidence'][0]['question']}]})
    assert not accepted and checks[0]['code'] == 'unverified_asr_answer'


@pytest.mark.parametrize('candidate', ['我问的是上次。请你回忆一下。我想是五月。', '当时天气挺冷。其实也没几个人。我想是五月。'])
def test_unconfirmed_multisentence_officer_speech_cannot_fill_as_an_answer(candidate):
    question = '你什么时候到的？'
    sources = [{'segment_id':'s', 'text':question+candidate, 'recognition_run_id':'r', 'speaker':'unknown'}]
    fact = {'label':'q1','value':candidate,'segment_ids':['s'],
            'evidence':[{'segment_id':'s','question':question,'answer':candidate}]}
    accepted,checks=check_answers([fact],sources,{'fields':[{'id':'q1','label':question}]})
    assert not accepted and checks[0]['code'] in {'question_in_answer','unverified_asr_answer'}


def test_confirmed_officer_context_is_available_to_model_rules_and_evidence(monkeypatch):
    from types import SimpleNamespace
    from backend.app import record_asr_results, records_ai
    from backend.app.record_candidates import pair_qa, rule_facts
    from backend.app.config import Settings
    from backend.app.records import input_segments
    parts = ['先核对一下时间。','你哪天入境的？','我问的是最近一次。','我记得是上周五。']
    segments = [SimpleNamespace(id=str(i),text=t,speaker='interviewer' if i<3 else 'interviewee') for i,t in enumerate(parts)]
    monkeypatch.setattr(record_asr_results, 'metadata', lambda s: dict(recognition_run_id='run',channel_id=0,speaker_id=int(s.id=='3'),
                        asr_original_text=s.text,asr_start_ms=int(s.id)*1000,asr_end_ms=int(s.id)*1000+850))
    full = '\n'.join(parts[:3])
    template = {'fields':[{'id':'q1','label':'入境日期','question_aliases':[full]}]}
    sources = input_segments(segments)
    import json
    monkeypatch.setattr(records_ai,'provider_config',lambda *_: ('deepseek','deepseek-chat','test'))
    message = records_ai.analysis_payload(sources,Settings(),template)['messages'][1]
    assert json.loads(message['content'])['qa_options']==[{'id':'p0001','question':full,'answer':parts[-1]}]
    facts = resolve_selections({'key_facts':[{'label':'q1','qa_ids':['p0001']}]},sources)
    accepted,_ = check_answers(facts['key_facts'],sources,template)
    ref, = accepted[0]['evidence_spans']
    assert ref['question']==full and ref['quote']==parts[-1]
    assert [q['segment_id'] for q in ref['question_sources']]==['0','1','2']
    pair, = pair_qa(segments,template)
    assert pair['question']==full and rule_facts([pair])[0]['value']==parts[-1]
    segments[2].text = '你的证件号码是多少？'
    template['fields'][0]['question_aliases'].append(segments[2].text)
    assert not rule_facts(pair_qa(segments,template))
    from backend.app.record_selection import paired_options
    assert not paired_options(input_segments(segments))


def split_reply():
    lines = ['你们学校有什么专业呢？', '我们学校有就公路系，', '还有信息。', '不过我不确定。']
    sources = [dict(segment_id=str(i), text=line, recognition_run_id='r', channel_id=0,
                    speaker='interviewer' if i==0 else 'interviewee', speaker_id=1 if i==0 else 2,
                    start_ms=i*1000, end_ms=i*1000+900) for i,line in enumerate(lines)]
    return sources, {'fields':[{'id':'q001','label':lines[0]}]}


def test_split_answer_preserves_each_quote_and_rejects_incomplete_legacy_evidence():
    sources,template=split_reply()
    selection={'key_facts':[{'label':'q001','qa_ids':['p0001']}]}
    fact,=resolve_selections(selection,sources)['key_facts']
    accepted,_=check_answers([fact],sources,template)
    assert accepted[0]['value']=='我们学校有就公路系，\n还有信息。\n不过我不确定。'
    assert [(s['segment_id'],s['quote']) for s in accepted[0]['evidence_spans']]==[
        (s['segment_id'],s['text']) for s in sources[1:]]
    for order in ([0], [1,2], [0,2], [2,1,0], [0,0,2]):
        partial=[fact['evidence'][i] for i in order]
        bad=dict(fact,evidence=partial,value='\n'.join(e['answer'] for e in partial))
        accepted,checks=check_answers([bad],sources,template)
        assert not accepted and checks[0]['code']=='incomplete_answer'


@pytest.mark.parametrize('changed', [dict(speaker_id=None), dict(speaker_id=3), dict(channel_id=1),
    dict(recognition_run_id='other'),dict(start_ms=6000,end_ms=6500)])
def test_unverified_continuation_cannot_leave_a_partial_answer(changed):
    from backend.app.record_selection import paired_options
    sources,_=split_reply()
    sources[2].update(changed)
    assert paired_options(sources)==[]


def test_next_officer_starts_a_separate_pair_after_supplement():
    from backend.app.record_selection import paired_options
    sources,_=split_reply()
    sources += [{**sources[0],'segment_id':'q2','text':'你从哪里来？'},
                {**sources[1],'segment_id':'a2','text':'北京。'}]
    pairs=paired_options(sources)
    assert [p['answer_ids'] for p in pairs]==[['u0002','u0003','u0004'],['u0006']]


def test_split_answer_reaches_ordinary_template_and_saved_evidence(client,cloud):
    import httpx,json
    from backend.app import records as r
    from backend.tests.test_qa_drafts import seed
    sources,template=split_reply()
    row,_=seed(client,tuple(s['text'] for s in sources),tuple(s['speaker_id'] for s in sources))
    with r.write_session(client.app.state.engine) as db:
        rec=db.get(r.RecordSession,row['id'])
        rec.template_snapshot={**template,'id':'eval','version':'1','name':'专业询问'}
        for seg,s in zip(r.ordered_segments(db,rec.id),sources):
            seg.speaker=s['speaker']
    def choose(request):
        prompt=json.loads(json.loads(request.content)['messages'][1]['content'])
        option,=prompt['qa_options']
        assert '还有信息。' in option['answer'] and '不过我不确定。' in option['answer']
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
            'summary':'匹配','key_facts':[{'label':'q001','qa_ids':[option['id']]}],'issues':[]})}}]})
    enable_ai(client,'deepseek')
    cloud.respond=choose
    url=f"/api/records/{row['id']}"
    revision=client.get(url).json()['revision']
    res=client.post(url+'/analyze',json={'confirmed':True,'expected_revision':revision})
    assert res.status_code==200,res.text
    saved=client.get(url).json()
    expected='\n'.join(s['text'] for s in sources[1:])
    assert saved['template_snapshot']['values']['q001']==saved['candidates'][0]['value']==expected
    assert expected in saved['rendered_text']
    evidence=saved['analysis']['key_facts'][0]['evidence_spans']
    assert [(s['segment_id'],s['start'],s['end']) for s in evidence]==[
        (s['id'],0,len(s['text'])) for s in saved['segments'][1:]]


def paused_reply():
    sources, template = split_reply()
    for source in sources[2:]:
        source['start_ms'] += 4500
        source['end_ms'] += 4500
    return sources, template


@pytest.mark.parametrize('gap_ms', [2100, 4600, 10000])
def test_confirmed_same_voice_pause_preserves_supplement_and_qualification(gap_ms):
    sources, template = paused_reply()
    shift = gap_ms - (sources[2]['start_ms'] - sources[1]['end_ms'])
    for source in sources[2:]:
        source['start_ms'] += shift
        source['end_ms'] += shift
    fact, = resolve_selections({'key_facts':[{'label':'q001','qa_ids':['p0001']}]}, sources)['key_facts']
    accepted, checks = check_answers([fact], sources, template)
    assert accepted[0]['value'] == '\n'.join(s['text'] for s in sources[1:])
    assert len(accepted[0]['evidence_spans']) == 3
    assert any(c['code']=='paused_reply' and c['status']=='attention' for c in checks)
    truncated = {**fact, 'evidence':fact['evidence'][:1], 'value':fact['evidence'][0]['answer']}
    assert not check_answers([truncated], sources, template)[0]


@pytest.mark.parametrize('change', [dict(speaker_id=None), dict(speaker_id=3), dict(channel_id=1),
    dict(recognition_run_id='other'), dict(voice_review=True), dict(speaker='unknown'),
    dict(start_ms=12000,end_ms=12500), dict(start_ms=1800,end_ms=2300),
    dict(session_intervals=[{'start_ms':20000,'end_ms':20500}])])
def test_pause_cannot_override_unknown_voice_overlap_or_recording_boundaries(change):
    from backend.app.record_selection import paired_options
    sources, _ = paused_reply()
    sources[2].update(change)
    assert not paired_options(sources)


def test_extended_reply_gap_does_not_extend_officer_context_or_voice_hints():
    from backend.app.record_speaker_turns import same_contiguous_voice, voice_continuation_hint
    sources, _ = paused_reply()
    assert not same_contiguous_voice(sources[1], sources[2])
    assert not voice_continuation_hint(sources[1], sources[2])


@pytest.mark.parametrize('answer', ['猪啊？', '北京？', '天下第一行书，莫非是王羲之？', '可能是上周五？'])
def test_confirmed_tentative_reply_keeps_question_mark_and_review_notice(answer):
    from types import SimpleNamespace
    from backend.app.record_candidates import pair_qa, rule_facts
    sources = [{'segment_id':'q','speaker':'interviewer','text':'请说一下具体情况。'},
               {'segment_id':'a','speaker':'interviewee','text':answer}]
    template = {'fields':[{'id':'q001','label':sources[0]['text']}]}
    fact, = resolve_selections({'key_facts':[{'label':'q001','qa_ids':['p0001']}]}, sources)['key_facts']
    accepted, checks = check_answers([fact], sources, template)
    assert accepted[0]['value'] == answer
    assert any(c['code']=='tentative_answer' and c['status']=='attention' for c in checks)
    pairs = pair_qa([SimpleNamespace(id=s['segment_id'],speaker=s['speaker'],text=s['text']) for s in sources],template)
    assert rule_facts(pairs)[0]['value'] == answer
    sources[1]['speaker'] = 'unknown'
    assert not check_answers([fact],sources,template)[0]


@pytest.mark.parametrize('answer', ['没有。你带了护照？', '对，李四什么时候来的？', '你说哪天？',
    '王某是谁？', '去过吗？', '是不是他？', '不知道。请说一下你的姓名？', '北京？我不知道。',
    '我问的是上次？', '带没带？', '去不去？'])
def test_confirmed_role_does_not_allow_question_text_to_leak_into_answer(answer):
    sources = [{'segment_id':'q','speaker':'interviewer','text':'请说一下具体情况。'},
               {'segment_id':'a','speaker':'interviewee','text':answer}]
    fact, = resolve_selections({'key_facts':[{'label':'q001','qa_ids':['p0001']}]}, sources)['key_facts']
    assert not check_answers([fact],sources,{'fields':[{'id':'q001','label':sources[0]['text']} ]})[0]


def test_question_only_template_without_pairs_stays_empty_without_cloud_request(client,cloud):
    from backend.app import records as r
    enable_ai(client, 'deepseek')
    row = with_segments(client)
    with r.write_session(client.app.state.engine) as db:
        record=db.get(r.RecordSession,row['id'])
        record.template_snapshot={'id':'no-pairs','version':'1','name':'核对',
            'fields':[{'id':'q001','label':'你带证件了吗？'}],'values':{}}
    row=client.get(f"/api/records/{row['id']}").json()
    response=segments(client,row,[editable(row['segments'][0],speaker='unknown',text='尚未核对这段发言。')])
    assert response.status_code==200,response.text
    row=response.json()
    response=client.post(f"/api/records/{row['id']}/analyze",json={'confirmed':True,'expected_revision':row['revision']})
    assert response.status_code==200,response.text
    saved=client.get(f"/api/records/{row['id']}").json()
    assert not cloud.calls
    assert not saved['candidates'] and not saved['template_snapshot']['values']
    assert saved['template_snapshot']['quality_checks'][0]['code']=='no_qa_options'


def test_unconfirmed_word_template_limits_request_to_metadata():
    import json
    from backend.app.records_ai import analysis_payload
    from backend.app.schemas import Settings
    from backend.app.record_templates import CASE_TEMPLATES
    settings = Settings()
    settings.ai.enabled = True
    settings.ai.providers.siliconflow.enabled = True
    from pydantic import SecretStr
    settings.ai.providers.siliconflow.api_key = SecretStr('test-key')
    payload = analysis_payload([{'segment_id':'s','speaker':'unknown','recognition_run_id':'r',
        'text':'你的家庭情况？我有一个孩子。'}], settings, CASE_TEMPLATES[0])
    fields = json.loads(payload['messages'][1]['content'])['template_fields']
    assert fields and all(not f['id'].startswith('q') for f in fields)
    assert payload['max_tokens'] <= 4000


def test_word_template_with_unconfirmed_asr_avoids_empty_model_generation(monkeypatch):
    from pydantic import SecretStr
    from backend.app import records_ai
    from backend.app.schemas import Settings
    from backend.app.record_templates import CASE_TEMPLATES
    settings = Settings()
    settings.ai.enabled = True
    settings.ai.providers.siliconflow.enabled = True
    settings.ai.providers.siliconflow.api_key = SecretStr('test-key')
    monkeypatch.setattr(records_ai, 'bounded_post', lambda *a, **k: pytest.fail('No grounded QA to send'))
    result = records_ai.analyze_segments([{'segment_id':'s','speaker':'unknown','recognition_run_id':'r',
        'text':'你的家庭情况？我有一个孩子。'}],settings,CASE_TEMPLATES[0])
    assert result['key_facts'] == []
    assert result['quality_checks'][0]['code'] == 'no_qa_options'
