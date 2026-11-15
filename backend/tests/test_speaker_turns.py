from backend.tests.test_speaker_roles import db, seed
from backend.tests.test_asr_results import output
from backend.app.record_asr_results import save_result,sentences
from backend.app.record_speaker_roles import map_role,review_sentence
from backend.app.record_speaker_turns import build_turns

def review(db,ss,roles,intents):
    for s,role,intent in zip(ss,roles,intents):
        review_sentence(db,'record',s.id,[dict(start=0,end=len(s.data['text']),role=role,intent=intent)],0,'actor')

def test_confirmed_answers_merge_but_questions_do_not(db):
    run=save_result(db,'record','audio:x',output(speakers=(0,0,1,1),texts=('你叫什么？','你住哪里？','张某。','住甲地。')));ss=sentences(db,run)
    review(db,ss,['OFFICER']*2+['INTERVIEWEE']*2,['QUESTION']*2+['ANSWER','CONTINUATION'])
    turns=build_turns(db,[(run,ss)])
    assert len(turns)==3 and len(turns[-1]['sources'])==2
    assert turns[-1]['text']=='张某。\n住甲地。'

def test_unknown_and_cross_run_stay_separate(db):
    run,ss=seed(db);other,os=seed(db,'two')
    assert len(build_turns(db,[(run,ss),(other,os)]))==4
    for rr,rows in [(run,ss),(other,os)]:review(db,rows,['INTERVIEWEE']*2,['ANSWER']*2)
    assert len(build_turns(db,[(run,ss),(other,os)]))==4 # distinct voices too

def test_manual_split_keeps_text_not_fake_time(db):
    run,ss=seed(db);s=ss[0];original=dict(s.data)
    review_sentence(db,'record',s.id,[dict(start=0,end=2,role='OFFICER',intent='QUESTION'),dict(start=2,end=len(s.data['text']),role='INTERVIEWEE',intent='ANSWER')],0,'actor')
    turns=build_turns(db,[(run,ss)])
    assert ''.join(t['text'] for t in turns[:2])==s.data['text']
    assert all(t['sources'][0]['start_ms'] is None for t in turns[:2])
    assert s.data==original

def test_discontinuous_and_overlap_do_not_merge(db):
    data=output(speakers=(1,1));data['segments'][1]['start_ms']=500
    run=save_result(db,'record','audio:x',data);ss=sentences(db,run)
    review(db,ss,['INTERVIEWEE']*2,['ANSWER']*2)
    assert len(build_turns(db,[(run,ss)]))==2


def test_gap_between_audio_ranges_prevents_merge(db):
    data=output(speakers=(1,1))
    mapping=[dict(batch_start_sample=0,batch_end_sample=16000,session_start_sample=0,sample_rate=16000),dict(batch_start_sample=16000,batch_end_sample=32000,session_start_sample=160000,sample_rate=16000)]
    run=save_result(db,'record','capture:session:batch',data,intervals=mapping);ss=sentences(db,run)
    review(db,ss,['INTERVIEWEE']*2,['ANSWER']*2)
    ts=build_turns(db,[(run,ss)])
    assert len(ts)==2
    assert ts[1]['sources'][0]['session_intervals'][0]['start_ms']==10000
