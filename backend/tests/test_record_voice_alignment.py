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


@pytest.mark.parametrize('cloud', ['你知道吗？对的。', '你知道吗？', '你知道吗？不是啊。'])
def test_isolated_disputed_or_missing_reply_is_a_listening_hint(cloud):
    pytest.importorskip('rapidfuzz')
    tokens = [(ch, 200*i, 200*i+150) for i, ch in enumerate('你知道吗')]
    tokens.extend((ch, 2200+180*i, 2360+180*i) for i, ch in enumerate('不知道'))
    parts = aligned_segments(cloud, tokens, [(1,False)]*4 + [(2,False)]*3, windows=[(4,7)])
    assert ''.join(p['text'] for p in parts) == cloud
    hints = [p for p in parts if p.get('local_alternatives')]
    assert len(hints) == 1 and hints[0]['voice_review'] and hints[0]['speaker'] == 'unknown'
    assert hints[0]['local_alternatives'] == [{'text':'不知道', 'start_ms':2200, 'end_ms':2720, 'speaker_id':2}]
    if cloud.endswith('不是啊。'):
        assert hints[0]['text'] == '不是啊。'


def test_pair_windows_keep_quiet_reply_and_original_time(monkeypatch):
    np = pytest.importorskip('numpy')
    from backend.app import record_voice_audio as voice
    rate = voice.RATE
    audio = np.zeros(3*rate, dtype=np.float32)
    audio[:rate] = .15
    audio[2*rate:] = .004
    ranges = list(voice.pair_windows(audio))
    assert ranges == [(0, 24000), (24000, len(audio))]
    def decode(_, clip):
        if len(clip) == len(audio):
            return [('问', 200, 600)]
        assert np.max(clip) > .03
        return [('答', 650, 850)] if np.max(clip) < .1 else [('问', 300, 700)]
    monkeypatch.setattr(voice, 'local_tokens', decode)
    tokens, windows = voice.pair_tokens(None, audio)
    assert tokens == [('问', 200, 600), ('答', 2050, 2250)]
    assert windows == [(0, 1), (1, 2)]
    assert audio[-1] == np.float32(.004)


@pytest.mark.parametrize('late_switch', [True, False])
def test_pair_recovers_unpunctuated_reply_onset_with_acoustic_support(late_switch):
    pytest.importorskip('rapidfuzz')
    text = '确定没偷渡吗我不确定'
    at = text.index('我')
    words = []
    for i, ch in enumerate(text):
        start = i * 150 + (300 if i >= at and not late_switch else 0)
        words.append((ch, start, start + 130))
    voices = [(1 if not late_switch or i <= at else 2, False) for i in range(len(text))]
    parts = aligned_segments(text, words, voices, paired_dialogue=True)
    assert [s['text'] for s in parts] == [text[:at], '我不确定']
    assert all(s['voice_review'] and s['speaker'] == 'unknown' for s in parts)
    if not late_switch:
        assert [s['speaker_id'] for s in parts] == [1, 1]
    contiguous = [(ch, i*150, i*150+130) for i, ch in enumerate(text)]
    assert len(aligned_segments(text, contiguous, [(1,False)]*len(text), paired_dialogue=True)) == 1
