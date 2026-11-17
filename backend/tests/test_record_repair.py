import json
import httpx
import pytest
from backend.tests.test_records import client,cloud,enable_ai,with_audio,segments,editable
from backend.app.record_quality import check_answers
from backend.app.record_semantic import review_matches


def test_retranscribe_returns_candidate_without_overwriting_original(client,cloud,monkeypatch):
    from backend.app import record_media
    enable_ai(client);row=with_audio(client)
    row=segments(client,row,[{'audio_id':row['audio_clips'][0]['id'],'text':'我没有重大疾病，不申请回','speaker':'unknown','start_ms':1000,'end_ms':3000}]).json()
    monkeypatch.setattr(record_media,'extract_speech_window',lambda *args:b'fictional-window')
    from backend.app import records_ai
    monkeypatch.setattr(records_ai,'normalize_for_asr',lambda *args, **kwargs:(b'fictional','mp3','audio/mpeg'))
    cloud.respond=lambda _:httpx.Response(200,json={'text':'我没有重大疾病，不申请回避。'})
    path=f"/api/records/{row['id']}/qa-retranscribe";body={'expected_revision':row['revision'],'confirmed':True,'segment_id':row['segments'][0]['id']}
    assert client.post(path,json={**body,'confirmed':False}).status_code==400
    response=client.post(path,json=body);assert response.status_code==200,response.text
    assert response.json()['text'].endswith('不申请回避。')
    current=client.get(f"/api/records/{row['id']}").json()
    assert current['segments']==row['segments'] and current['revision']==row['revision']
    assert client.post(path,json={**body,'segment_id':'another-record'}).status_code==422


def test_semantic_match_cannot_bypass_fabricated_answer_or_role():
    sources=[{'segment_id':'s','text':'护照怎么办的？我认为是真的，但请核查。','speaker':'interviewer'}]
    facts=[{'label':'q001','value':'是真的','segment_ids':['s'],'evidence':[{'segment_id':'s','question':'护照怎么办的？','answer':'我认为是真的，但请核查。'}]}]
    accepted,checks=check_answers(facts,sources,{'fields':[{'id':'q001','label':'证件真伪'}]}, {'q001'})
    assert not accepted and checks[0]['code']=='role_mismatch'


@pytest.mark.parametrize('content',[{'matches':[{'field_id':'invented','supported':True}]},{'matches':[{'field_id':'q1','supported':'true'}]},{'matches':[]},{}])
def test_bad_semantic_response_keeps_original_rejection(client,cloud,content):
    enable_ai(client)
    cloud.respond=lambda _:httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(content)}}]})
    assert not review_matches([{'label':'q1','evidence':[{'question':'怎么来的','answer':'坐车来的'}]}],[{'field_id':'q1','code':'question_mismatch'}],{'fields':[{'id':'q1','label':'入境交通工具'}]},client.app.state.store.current)


def test_real_short_audio_window_has_complete_wav_header_and_preserves_tail():
    import io,wave
    from backend.app.record_media import extract_speech_window,normalize_for_asr,conversion_available
    if not conversion_available():pytest.skip('FFmpeg required')
    original=io.BytesIO()
    pcm=b'\x00\x00'*31680+b'\xe8\x03'*320
    with wave.open(original,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(pcm)
    window=extract_speech_window(original.getvalue(),'wav',1000,2000)
    with wave.open(io.BytesIO(window)) as f:
        assert f.getnframes()==16000
        assert f.readframes(16000).endswith(b'\xe8\x03'*320)
    converted,fmt,_=normalize_for_asr(window,'wav')
    assert fmt=='mp3' and len(converted)>128
