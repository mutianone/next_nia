from copy import deepcopy
import pytest
from backend.app.record_dual_prefill import prefill
from backend.app.record_dual_asr import combine
from backend.tests.test_dual_asr import output

QUESTION = '你是否是人大代表或政协委员？'
TEMPLATE = {'fields': [{'id': 'q001', 'label': QUESTION}]}


def source(question, answer='不是。'):
    engines = {name: output([question, answer], name) for name in ('siliconflow', 'volcengine')}
    return [(str(i), s['dual_review']) for i, s in enumerate(combine(engines)['segments'])]


@pytest.mark.parametrize('prefix', [
    '', '请听清楚：', '下面核实一项情况，请听清楚，', '请你如实回答。', '现在确认一下，',
])
def test_neutral_instructions_do_not_prevent_full_answer(prefix):
    template, added = prefill(TEMPLATE, source(prefix + QUESTION))
    assert added == ['q001']
    assert template['values'] == {'q001': '不是。'}


@pytest.mark.parametrize('prefix', [
    '不是问你', '他问我', '假如', '如果明年当选，', '我不想问', '不要回答',
])
def test_qualified_quoted_or_negated_prompts_do_not_match(prefix):
    template, added = prefill(TEMPLATE, source(prefix + QUESTION))
    assert added == [] and not template.get('values')


def test_conflicting_supplement_does_not_fill_agreeing_prefix():
    engines = {name: output([QUESTION, '不是。', tail], name)
               for name, tail in [('siliconflow', '我没有担任职务。'), ('volcengine', '我担任职务。')]}
    groups = [(str(i), s['dual_review']) for i, s in enumerate(combine(engines)['segments'])]
    template, added = prefill(TEMPLATE, groups)
    assert added == [] and not template.get('values')


def test_existing_or_protected_field_is_preserved():
    for overrides in ({'values': {'q001': '人工修订。'}}, {'locked_fields': ['q001']}):
        template = {**deepcopy(TEMPLATE), **overrides}
        assert prefill(template, source('请听清楚：' + QUESTION)) == (template, [])


def test_question_spanning_segments_is_matched_without_dropping_characters():
    engines = {name: output(['请听清楚：你是否是人大代表或政协委', '员？', '不是。'], name)
               for name in ('siliconflow', 'volcengine')}
    for e in engines.values():
        e['segments'][1]['speaker_id'] = e['segments'][0]['speaker_id']
    groups = [(str(i), s['dual_review']) for i, s in enumerate(combine(engines)['segments'])]
    assert prefill(TEMPLATE, groups)[0]['values'] == {'q001': '不是。'}


def test_two_independent_transcripts_can_support_complete_supplement():
    from backend.app.record_dual_asr import local_verification
    engines = {name: output([QUESTION, answer], name) for name, answer in
               [('siliconflow', '不是。还有谢西。'), ('volcengine', '不是。还有信息。')]}
    text = QUESTION + '不是。还有信息。'
    lexical = [c for c in text if c.isalnum()]
    q_length = len([c for c in QUESTION if c.isalnum()])
    engines['siliconflow']['local_verification'] = {
        'text': ''.join(lexical),
        'words': [(c, 100*i, 100*i+90) for i,c in enumerate(lexical)],
        'decisions': [(1 if i<q_length else 2,False) for i in range(len(lexical))],
    }
    evidence = local_verification(engines)
    assert evidence and evidence['verified_provider'] == 'volcengine'
    result = combine(engines)
    groups = [(str(i), s['dual_review']) for i,s in enumerate(result['segments'])]
    template, added = prefill(TEMPLATE, groups)
    assert added == ['q001']
    assert template['values']['q001'].replace('\n', '') == '不是。还有信息。'
    assert '谢西' in result['raw_result']['engines']['siliconflow']['text']
    engines['siliconflow']['local_verification']['text'] = '不是'
    assert local_verification(engines) is None


def test_local_check_keeps_decimal_and_negation_differences():
    from backend.app.record_dual_asr import local_verification
    engines = {name: output([text], name) for name,text in
               [('siliconflow', '12.3元。'), ('volcengine', '别的金额。')]}
    engines['siliconflow']['local_verification'] = {'text':'123元'}
    assert local_verification(engines) is None
    engines['siliconflow']['text'] = '没有。'
    engines['siliconflow']['local_verification']['text'] = '有'
    assert local_verification(engines) is None


from pathlib import Path
import json
from backend.tests.test_record_capture import client
from backend.tests.test_records import create, upload
from backend.tests.test_dual_asr import settings
from backend.app import records

FROZEN = json.loads((Path(__file__).resolve().parents[2] / 'docs/dual-prefill-evidence-20261119.json').read_text())


@pytest.mark.parametrize('case', FROZEN, ids=lambda c: c['id'])
def test_synthesized_dialogues_through_transcribe_and_saved_template(client, monkeypatch, case):
    client.app.state.store.current = settings()
    row = upload(client, create(client, template_id='bride', scenario='marriage'), duration=60000).json()
    parts = [{'speaker':'unknown', 'text':g['candidates'][0]['text'], 'dual_review':g} for _,g in case['groups']]
    output = {'provider':'dual', 'model':'frozen-audio-evaluation',
              'text':'\n'.join(s['text'] for s in parts), 'segments':parts, 'raw_result':{}}
    monkeypatch.setattr(records, 'transcribe_audio', lambda *a, **kw: output)
    url = '/api/records/'+row['id']
    response = client.post(url+'/audio/'+row['audio_clips'][0]['id']+'/transcribe',
                           json={'expected_revision':row['revision'], 'confirmed':True, 'speaker_count':2})
    assert response.status_code == 200, response.text
    saved = client.get(url).json()
    from backend.app.record_dual_prefill import surface
    actual = {k:v for k,v in saved['template_snapshot']['values'].items() if k.startswith('q')}
    assert {k:surface(v) for k,v in actual.items()} == {k:surface(v) for k,v in case['expected'].items()}
    if case['id'] == '03':
        run = client.get(url+'/dual-review').json()['runs'][0]
        reviewed = client.post(url+'/dual-review/'+run['id'], json={
            'expected_revision':saved['revision'], 'confirmed':True,
            'choices':['siliconflow']*len(run['groups'])})
        assert reviewed.status_code == 200, reviewed.text
        # Selecting the conflicting cloud version invalidates the prior consensus.
        assert not reviewed.json()['template_snapshot']['values'].get('q004')
