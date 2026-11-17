import pytest
from backend.app.record_voice_alignment import aligned_segments
from backend.app.record_speaker_turns import reviewed_word_times
from backend.app.record_quality import check_answers
from backend.app.records import TranscribeRequest


def test_short_answer_next_question_and_unmatched_negative():
    pytest.importorskip('rapidfuzz')
    text = '我确定你是哪天到的？'
    words = [(c,i*180,(i+1)*180) for i,c in enumerate(text[:-1])]
    decisions = [(1,False)]*4 + [(2,False)]*(len(words)-4)
    parts = aligned_segments(text,words,decisions)
    assert [(p['speaker_id'],p['text']) for p in parts] == [(1,'我确定'),(2,'你是哪天到的？')]
    assert all(p['voice_review'] for p in parts)
    assert ''.join(p['text'] for p in parts) == text
    # A missing negation before an otherwise matching cloud transcript is disputed.
    part, = aligned_segments('知道',[('不',0,130),('知',180,290),('道',310,490)],[(2,False)]*3)
    assert part['voice_review'] and part['text'] == '知道'
    unmapped, = aligned_segments('嗯。',[],[])
    assert unmapped['speaker_id'] is None and unmapped['start_ms'] is None and unmapped['voice_review']


def test_reviewed_split_uses_whole_original_words_only():
    sentence = {'text':'确定吗？我确定。','word_boundaries':True,'words':[
        {'text':'确定','begin_time':50,'end_time':220}, {'text':'吗','punctuation':'？','begin_time':225,'end_time':380},
        {'text':'我','begin_time':400,'end_time':550}, {'text':'确定','punctuation':'。','begin_time':570,'end_time':840}]}
    assert reviewed_word_times(sentence,4,8) == (400,840)
    assert reviewed_word_times(sentence,1,4) == (None,None)
    sentence['text'] = '确定吗？我不确定。'
    assert reviewed_word_times(sentence,4,9) == (None,None)


def test_disputed_question_cannot_fill_even_with_long_answer():
    question, answer = '你哪天到的？', '我是上星期五到这里的。'
    sources = [{'segment_id':'a','speaker':'unknown','text':question,'voice_review':True},
               {'segment_id':'b','speaker':'interviewee','text':answer}]
    fact = {'label':'q1','value':answer,'evidence':[{'segment_id':'b','question_segment_id':'a','answer':answer,'question':question}]}
    accepted, checks = check_answers([fact],sources,{'fields':[{'id':'q1','label':question}]})
    assert not accepted and checks[0]['code'] == 'voice_boundary_review'


@pytest.mark.parametrize('bad',[True,1,101,3.2])
def test_speaker_count_validation(bad):
    with pytest.raises(ValueError):
        TranscribeRequest(expected_revision=1,speaker_count=bad)


def test_missing_question_does_not_join_two_short_answers_by_one_voice():
    pytest.importorskip('rapidfuzz')
    words = [(c,i*200,(i+1)*200) for i,c in enumerate('是的确定吗我确定')]
    parts = aligned_segments('是的，我确定。',words,[(2,False)]*2+[(1,False)]*3+[(2,False)]*3)
    assert [(p['speaker_id'],p['text']) for p in parts] == [(2,'是的，'),(2,'我确定。')]
    assert all(p['voice_review'] for p in parts)


def test_local_disagreement_flags_provider_groups_without_relabeling(monkeypatch):
    from backend.app import record_voice_audio as voice
    monkeypatch.setattr(voice,'samples',lambda *_: object())
    monkeypatch.setattr(voice,'models',lambda *_: (object(),None))
    # A single cloud ID spans two locally distinct voices. ID 11 is consistent.
    monkeypatch.setattr(voice,'voice_decisions',lambda *args: [(4,False),(9,False),(6,False)])
    segments = [{'speaker_id':sp,'text':txt,'words':[{'text':txt,'begin_time':i*300,'end_time':(i+1)*300}]}
                for i,(sp,txt) in enumerate([(10,'确定吗'),(10,'确定'),(11,'还有谁')])]
    output=voice.annotate(b'','wav',{'provider':'aliyun','segments':segments},3)
    assert [(s['speaker_id'],s['voice_review']) for s in output['segments']] == [(10,True),(10,True),(11,False)]
