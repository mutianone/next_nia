import json
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.tests.test_records import client, create, upload
from backend.app.records import AudioClip, TranscriptionAttempt, RecordSegment, RecordAnalysis, RecordSession, RecordVersion, version


def seeded(client):
    row=create(client,template_id='bride')
    row=upload(client,row).json();a=row['audio_clips'][0]['id']
    row=upload(client,row,raw=b'ID3\x04\x00\x00\x00\x00\x00\x10'+b'other-synthetic-audio'*10).json();b=next(x['id'] for x in row['audio_clips'] if x['id']!=a)
    with Session(client.app.state.engine) as s:
        target=s.get(RecordSession,row['id'])
        for index,clip in enumerate([a,b]):
            attempt=TranscriptionAttempt(record_id=row['id'],audio_id=clip,status='succeeded',text=f'PRIVATE_AUDIO_{index}',provider='test',model='test',input_revision=target.revision)
            s.add(attempt);s.flush()
            s.add(RecordSegment(record_id=row['id'],audio_id=clip,position=index,text=f'PRIVATE_AUDIO_{index}',source='asr',transcription_id=attempt.id))
        s.flush();version(s,target,'transcribed')
        s.add(RecordAnalysis(record_id=row['id'],status='succeeded',summary='PRIVATE_AUDIO_0 mixed',key_facts=[],issues=[],input_segments=[{'segment_id':x.id,'text':x.text} for x in s.scalars(select(RecordSegment))],input_hash='test',input_revision=target.revision,provider='test',model='test'))
        target.template_snapshot={**target.template_snapshot,'generated':True,'values':{'name':'PRIVATE_AUDIO_0'},'sources':{},'source_hash':'test'}
        version(s,target,'analyzed');s.commit()
    return client.get('/api/records/'+row['id']).json(),a,b


def remove(client,row,a,**changes):
    return client.request('DELETE',f"/api/records/{row['id']}/audio/{a}",json={'expected_revision':row['revision'],'confirmed':True,**changes})


def test_delete_clears_data_history_and_keeps_other_audio(client):
    row,a,b=seeded(client)
    # Unlinking the current segment must not erase its original provenance.
    with Session(client.app.state.engine) as s:
        segment=s.scalar(select(RecordSegment).where(RecordSegment.audio_id==a));segment.audio_id=None;s.commit()
    response=remove(client,row,a);assert response.status_code==200,response.text
    data=response.json()
    assert [x['id'] for x in data['audio_clips']]==[b]
    assert [x['text'] for x in data['segments']]==['PRIVATE_AUDIO_1']
    assert len(data['transcription_attempts'])==1
    assert data['analysis'] is None and not data['analyses']
    assert not data['template_snapshot'].get('generated')
    assert 'PRIVATE_AUDIO_0' not in json.dumps(data)
    with Session(client.app.state.engine) as s:
        assert s.get(AudioClip,a) is None
        for v in s.scalars(select(RecordVersion).where(RecordVersion.record_id==row['id'])):
            assert 'PRIVATE_AUDIO_0' not in json.dumps(v.snapshot)
            assert a not in json.dumps(v.snapshot)
    assert client.get(f"/api/records/{row['id']}/audio/{a}/file").status_code==404
    assert client.get(f"/api/records/{row['id']}/audio/{b}/file").status_code==200
    assert client.get(f"/api/records/{row['id']}/export.docx").status_code==409
    response=remove(client,data,b);assert response.status_code==200
    assert not response.json()['audio_clips'] and not response.json()['segments']


@pytest.mark.parametrize('change,status',[({'confirmed':False},422),({'expected_revision':999},409)])
def test_delete_requires_confirmation_and_current_revision(client,change,status):
    row,a,b=seeded(client)
    assert remove(client,row,a,**change).status_code==status
    assert len(client.get('/api/records/'+row['id']).json()['audio_clips'])==2


def test_delete_rejects_foreign_clip_locked_and_processing(client):
    row,a,b=seeded(client);other=create(client)
    assert remove(client,other,a).status_code==404
    with Session(client.app.state.engine) as s:
        attempt=s.scalar(select(TranscriptionAttempt).where(TranscriptionAttempt.audio_id==a));attempt.status='processing';s.commit()
    assert remove(client,row,a).status_code==409
    with Session(client.app.state.engine) as s:
        attempt=s.scalar(select(TranscriptionAttempt).where(TranscriptionAttempt.audio_id==a));attempt.status='succeeded'
        record=s.get(RecordSession,row['id']);record.status='archived';s.commit()
    row=client.get('/api/records/'+row['id']).json()
    assert remove(client,row,a).status_code==409
