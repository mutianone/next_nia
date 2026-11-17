import pytest
from backend.app.record_speech import normalize_turns
from backend.app.records_ai import RecordAIError
from backend.app.record_qa_review import acoustic_proposal,validate_turns


def test_turns_preserve_anonymous_identity_and_timestamps():
    turns=normalize_turns({'segments':[{'text':'没有。但日期不确定。','speaker':'2','start':1.2,'end':3.5}]})
    assert turns==[{'text':'没有。但日期不确定。','speaker':'unknown','speaker_id':'2',
                   'start_ms':1200,'end_ms':3500,'timestamp_source':'PROVIDER'}]


@pytest.mark.parametrize('patch',[{'start':float('nan')},{'end':-1},{'speaker':'../../secret'},{'text':''}])
def test_invalid_provider_segments_fail_closed(patch):
    with pytest.raises(RecordAIError):normalize_turns({'segments':[{'text':'回答','speaker':'1','start':0,'end':1,**patch}]})


def test_acoustic_proposal_never_uses_same_speaker_id_across_clips():
    sources=[{'id':str(i),'audio_id':audio,'text':'[说话人 1] 你好。','start_ms':0,'end_ms':1000} for i,audio in enumerate(['a','a','b'])]
    turns,issues=validate_turns(acoustic_proposal(sources),sources)
    assert turns[0]['voice_group']==turns[1]['voice_group']!=turns[2]['voice_group']
    assert all(t['speaker']==t['suggested_role']=='unknown' for t in turns)
    assert not issues


from backend.tests.test_records import client,cloud,enable_ai,with_audio,asr,segments,editable


def test_diarized_upload_preserves_time_then_confirmed_roles_reach_document(client,cloud):
    import httpx,json
    enable_ai(client);row=with_audio(client)
    cloud.respond=lambda _:httpx.Response(200,json={'text':'原始聚合文本','segments':[
        {'text':'请说一下护照情况。','speaker':'1','start':0,'end':1},
        {'text':'我带了护照。','speaker':'2','start':1.2,'end':2.5},
        {'text':'但有效期记不住。','speaker':'2','start':2.6,'end':4}]})
    response=asr(client,row);assert response.status_code==200,response.text
    row=response.json();assert len(row['segments'])==3
    assert all(x['speaker']=='unknown' and x['timestamp_source']=='asr' for x in row['segments'])
    assert row['segments'][1]['start_ms']==1200
    response=client.post(f"/api/records/{row['id']}/qa-proposal",json={'expected_revision':row['revision'],'confirmed':True})
    assert response.status_code==200,response.text
    assert len(cloud.calls)==1  # Acoustic boundaries require no second cloud guessing call.
    assert len(response.json()['turns'])==3
    assert [s['text'] for s in row['segments']]==['请说一下护照情况。','我带了护照。','但有效期记不住。']
    from backend.app import records
    from backend.app.record_selection import paired_options,resolve_selections
    with records.Session(client.app.state.engine) as session:
        recognition=session.get(records.asr_results.RecognitionRun,row['segments'][0]['recognition_run_id'])
        assert recognition.raw_result['text']=='原始聚合文本'
        assert recognition.raw_result['segments'][2]['text']=='但有效期记不住。'
        assert not paired_options(records.input_segments(records.ordered_segments(session,row['id'])))
    row=segments(client,row,[editable(s,speaker='interviewer' if i==0 else 'interviewee')
                             for i,s in enumerate(row['segments'])]).json()
    with records.Session(client.app.state.engine) as session:
        inputs=records.input_segments(records.ordered_segments(session,row['id']))
    assert inputs[0]['speaker']=='interviewer' and len(paired_options(inputs))==1
    resolved=resolve_selections({'key_facts':[{'label':'q001','qa_ids':['p0001']}]},inputs)
    assert resolved['key_facts'][0]['value']=='我带了护照。\n但有效期记不住。'
    assert len(resolved['key_facts'][0]['evidence'])==2


def test_fun_asr_does_not_skip_semantic_boundary_review(monkeypatch):
    from backend.app import record_qa_review as qa
    from backend.app.schemas import Settings
    import json
    settings=Settings();settings.ai.speech.provider='aliyun'
    text='[说话人 1] 我骑车来的，你和谁一起住？'
    sources=[{'id':'s','audio_id':'a','text':text,'start_ms':100,'end_ms':2000}]
    calls=[]
    def respond(*args,**kwargs):
        calls.append(kwargs)
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'turns':[
            {'segment_id':'s','text':'我骑车来的，','suggested_role':'interviewee'},
            {'segment_id':'s','text':'你和谁一起住？','suggested_role':'interviewer'}]})}}]}
    monkeypatch.setattr(qa,'bounded_post',respond)
    from backend.app import record_qa_boundaries
    monkeypatch.setattr(record_qa_boundaries,'split_candidates',lambda sources,*args:(sources,{s['id']:s['id'] for s in sources}))
    turns,issues=qa.propose(sources,settings,'siliconflow','test','test-key')
    assert len(calls)==1 and len(turns)==2
    assert ''.join(t['text'] for t in turns)==text
    assert all(t['speaker']=='unknown' and t['voice_group'] is None for t in turns)
    assert all(t['start_ms'] is None and t['listen_start_ms']==100 for t in turns)


def test_conflicting_acoustic_roles_disable_bulk_assignment():
    sources=[{'id':str(i),'audio_id':'a','text':text,'start_ms':i*1000,'end_ms':(i+1)*1000} for i,text in enumerate(['[说话人 0] 知道了吗？','[说话人 0] 知道了。'])]
    turns,_=validate_turns({'turns':[{'segment_id':s['id'],'text':s['text'],'suggested_role':role} for s,role in zip(sources,['interviewer','interviewee'])]},sources)
    assert all(t['voice_group'] is None and t['speaker']=='unknown' for t in turns)


def test_boundary_split_preserves_negation_and_rejects_loss():
    from backend.app.record_qa_boundaries import validate_splits
    source={'id':'x','text':'没去过，你住哪里？'}
    parts,origins=validate_splits({'splits':[{'segment_id':'x','parts':['没去过，','你住哪里？']}]},[source])
    assert ''.join(p['text'] for p in parts)==source['text']
    assert set(origins.values())=={'x'}
    with pytest.raises(ValueError):validate_splits({'splits':[{'segment_id':'x','parts':['去过，','你住哪里？']}]},[source])


def test_boundary_anchors_are_unique_and_preserve_every_character():
    from backend.app.record_qa_boundaries import validate_splits
    source={'id':'x','text':'我不清楚。你什么时候来的？上午。'}
    parts,_=validate_splits({'splits':[{'segment_id':'x','starts':['你什么时候','上午。']}]},[source])
    assert [p['text'] for p in parts]==['我不清楚。','你什么时候来的？','上午。']
    with pytest.raises(ValueError):validate_splits({'splits':[{'segment_id':'x','starts':['不存在的内容']}]},[source])
    with pytest.raises(ValueError):validate_splits({'splits':[{'segment_id':'x','starts':['上午。','你什么时候']}]},[source])


def test_embedded_question_candidates_and_quoted_questions():
    from backend.app.record_qa_boundaries import split_candidates
    rows=[{'id':'a','text':'他告诉我乘车路线，张某本人有没有陪你去？'},
          {'id':'b','text':'我不清楚到广州之后去过哪里，先在旅馆住了一天。'},
          {'id':'c','text':'他说“你住哪里？”我没有回答。'},
          {'id':'d','text':'我不知道他有没有去，不能替他说。'}]
    parts,origins=split_candidates(rows)
    grouped={s['id']:[p['text'] for p in parts if origins[p['id']]==s['id']] for s in rows}
    assert grouped['a']==['他告诉我乘车路线，','张某本人有没有陪你去？']
    assert grouped['b']==['我不清楚','到广州之后去过哪里，','先在旅馆住了一天。']
    assert grouped['c']==[rows[2]['text']] and grouped['d']==[rows[3]['text']]
    assert all(''.join(grouped[s['id']])==s['text'] for s in rows)
