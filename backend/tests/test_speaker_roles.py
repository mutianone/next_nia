import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from backend.app.database import Base
from backend.app.record_asr_results import save_result, sentences
from backend.app.record_speaker_roles import map_role, review_sentence, sentence_parts, SpeakerRoleMapping
from backend.tests.test_asr_results import output

@pytest.fixture
def db():
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with Session(engine) as session:yield session
    engine.dispose()

def seed(db, task='one'):
    run=save_result(db,'record','audio:one',output(task));return run,sentences(db,run)

def test_mapping_scope_revision_and_override(db):
    run,ss=seed(db);other,other_ss=seed(db,'two')
    m=map_role(db,'record',run.id,0,0,'OFFICER',0,'actor')
    assert m.confirmed_by=='actor' and m.revision==1
    assert sentence_parts(db,ss[0])[0][0]['role']=='OFFICER'
    assert sentence_parts(db,other_ss[0])[0][0]['role']=='UNKNOWN'
    with pytest.raises(ValueError,match='冲突'):map_role(db,'record',run.id,0,0,'INTERVIEWEE',0,'actor')
    review_sentence(db,'record',ss[0].id,[dict(start=0,end=len(ss[0].data['text']),role='INTERVIEWEE',intent='CLARIFICATION')],0,'actor')
    map_role(db,'record',run.id,0,0,'OTHER',1,'actor2')
    assert sentence_parts(db,ss[0])[0][0]['role']=='INTERVIEWEE'
    assert len(list(db.scalars(select(SpeakerRoleMapping))))==2

def test_bad_scope_and_span_rejected(db):
    run,ss=seed(db)
    with pytest.raises(ValueError):map_role(db,'another',run.id,0,0,'OFFICER',0,'actor')
    with pytest.raises(ValueError):map_role(db,'record',run.id,1,0,'OFFICER',0,'actor')
    with pytest.raises(ValueError):review_sentence(db,'record',ss[0].id,[dict(start=1,end=3,role='OFFICER',intent='QUESTION')],0,'actor')
    assert sentence_parts(db,ss[0])[0][0]['role']=='UNKNOWN'

def test_channel_isolation_and_null_speaker(db):
    data=output();track=data['raw_result']['transcripts'][0]
    from copy import deepcopy
    from backend.app.record_asr_results import normalize_funasr
    other=deepcopy(track);other['channel_id']=1
    data['raw_result']['transcripts'].append(other)
    run=save_result(db,'record','audio:two',normalize_funasr(data['raw_result'],'channels'));ss=sentences(db,run)
    map_role(db,'record',run.id,0,0,'OFFICER',0,'actor')
    assert sentence_parts(db,ss[2])[0][0]['role']=='UNKNOWN'
    with pytest.raises(ValueError):map_role(db,'record',run.id,0,None,'OFFICER',0,'actor')


def test_legacy_review_ui_scopes_provider_labels_by_channel():
    from backend.app.record_qa_review import validate_turns
    sources=[dict(id=str(i),text='原文',audio_id='a',start_ms=0,end_ms=1000,recognition_run_id='run',speaker_id=0,channel_id=i) for i in (0,1)]
    result,_=validate_turns({'turns':[dict(segment_id=s['id'],text=s['text'],suggested_role='unknown') for s in sources]},sources)
    assert result[0]['voice_group']!=result[1]['voice_group']
