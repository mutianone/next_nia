import json
import httpx
import pytest
from backend.app.record_qa_review import validate_turns
from backend.app.records_ai import RecordAIError
from backend.tests.test_records import client,cloud,enable_ai,with_segments,segments,editable


def source(text='听明白了吗？听明白了。'):
    return {'id':'s1','text':text,'audio_id':'a1','start_ms':None,'end_ms':None}


def proposal(sid='s1'):
    return {'turns':[{'segment_id':sid,'text':'听明白了吗？','suggested_role':'interviewer'},
                     {'segment_id':sid,'text':'听明白了。','suggested_role':'interviewee'}]}


def test_exact_partition_never_confirms_roles_or_invents_times():
    turns,issues=validate_turns(proposal(),[source()])
    assert not issues and len(turns)==2
    assert ''.join(t['text'] for t in turns)==source()['text']
    assert all(t['speaker']=='unknown' and t['start_ms'] is None for t in turns)
    assert turns[1]['source_start']==len('听明白了吗？')


@pytest.mark.parametrize('text',['明白了。','不明白。','听明白了。听明白了。'])
def test_changed_or_dropped_text_falls_back_to_original(text):
    p=proposal();p['turns'][1]['text']=text
    turns,issues=validate_turns(p,[source()])
    assert issues and len(turns)==1 and turns[0]['text']==source()['text']
    assert turns[0]['suggested_role']=='unknown'


def test_split_does_not_claim_sentence_timestamps():
    s={**source(),'start_ms':5000,'end_ms':10000}
    turns,_=validate_turns(proposal(),[s])
    assert all(t['listen_start_ms']==5000 and t['start_ms'] is None and t['end_ms'] is None for t in turns)


def test_forged_source_rejected():
    with pytest.raises(RecordAIError):validate_turns(proposal('fake'),[source()])


def setup(client):
    row=with_segments(client,template_id='bride')
    return segments(client,row,[editable(row['segments'][0],text=source()['text'])]).json()


def test_propose_is_non_mutating_and_requires_consent(client,cloud):
    enable_ai(client,'deepseek');row=setup(client)
    sid=row['segments'][0]['id']
    cloud.respond=lambda _:httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(proposal(sid))}}]})
    path=f"/api/records/{row['id']}/qa-proposal"
    assert client.post(path,json={'expected_revision':row['revision'],'confirmed':False}).status_code==400
    response=client.post(path,json={'expected_revision':row['revision'],'confirmed':True})
    assert response.status_code==200,response.text
    assert len(response.json()['turns'])==2
    current=client.get(f"/api/records/{row['id']}").json()
    assert current['segments']==row['segments'] and current['revision']==row['revision']
    assert current['transcription_attempts']==row['transcription_attempts']


def test_concurrent_edit_invalidates_proposal(client,cloud):
    enable_ai(client,'deepseek');row=setup(client);sid=row['segments'][0]['id']
    def respond(_):
        assert segments(client,row,[editable(row['segments'][0],text='已经人工修改')]).status_code==200
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(proposal(sid))}}]})
    cloud.respond=respond
    response=client.post(f"/api/records/{row['id']}/qa-proposal",json={'expected_revision':row['revision'],'confirmed':True})
    assert response.status_code==409


def test_archived_record_cannot_prepare(client,cloud):
    enable_ai(client,'deepseek');row=setup(client)
    archived=client.patch(f"/api/records/{row['id']}",json={'expected_revision':row['revision'],'status':'archived'}).json()
    response=client.post(f"/api/records/{row['id']}/qa-proposal",json={'expected_revision':archived['revision'],'confirmed':True})
    assert response.status_code==409


def test_glued_officer_prompt_becomes_unconfirmed_split_without_text_changes():
    s=source('我看过了，不用再读说一下你的。')
    turns,issues=validate_turns({'turns':[{'segment_id':'s1','text':s['text'],'suggested_role':'interviewee'}]},[s])
    assert [t['text'] for t in turns]==['我看过了，不用再读','说一下你的。']
    assert ''.join(t['text'] for t in turns)==s['text']
    assert all(t['speaker']==t['suggested_role']=='unknown' for t in turns) and issues


def test_manual_turns_keep_original_attempt_and_audio_associations(client,cloud):
    enable_ai(client,'deepseek');row=setup(client);original=row['transcription_attempts']
    first=row['segments'][0]
    result=segments(client,row,[editable(first,speaker='interviewer',text='问：听明白了吗？'),
        {'audio_id':first['audio_id'],'start_ms':None,'end_ms':None,'speaker':'interviewee','text':'答：听明白了。'}])
    assert result.status_code==200,result.text
    updated=result.json()
    assert updated['transcription_attempts']==original
    assert [s['speaker'] for s in updated['segments']]==['interviewer','interviewee']
    assert all(s['audio_id']==first['audio_id'] for s in updated['segments'])
    assert updated['revision']==row['revision']+1
