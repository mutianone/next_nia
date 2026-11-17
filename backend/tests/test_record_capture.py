"""Synthetic continuous-capture acceptance. No paid cloud calls or business data."""
from datetime import datetime,timezone,timedelta
from uuid import uuid4
from types import SimpleNamespace
import io
import wave
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import Session
from backend.app import record_capture as c,records as r
from backend.app.config import SettingsStore
from backend.tests.test_records import create,enable_ai

@pytest.fixture
def client(tmp_path,monkeypatch):
    app=FastAPI();app.state.engine=create_engine(f'sqlite:///{tmp_path}/capture.sqlite3',connect_args={'check_same_thread':False})
    r.Base.metadata.create_all(app.state.engine);app.state.store=SettingsStore(tmp_path);app.state.store.load()
    @app.middleware('http')
    async def actor(request,call_next):
        request.state.user={'id':request.headers.get('x-test-user','synthetic-user'),'role':'business'}
        return await call_next(request)
    app.include_router(r.router)
    monkeypatch.setattr(r,'conversion_available',lambda:True)
    with TestClient(app) as client:
        enable_ai(client);yield client
    app.state.engine.dispose()

@pytest.fixture
def cloud(monkeypatch):
    calls=[]
    def transcribe(raw,*args):
        with wave.open(io.BytesIO(raw)) as f:assert f.getframerate()==16000
        calls.append('asr');return {'text':f'问：你住哪里？\n答：合成地址{calls.count("asr")}。'}
    def analyze(inputs,settings,template):
        calls.append('analysis');return {'summary':'合成摘要','key_facts':[{'label':template['fields'][0]['id'],'value':inputs[-1]['text'],'segment_ids':[inputs[-1]['segment_id']]}],'issues':[]}
    monkeypatch.setattr(c,'transcribe_audio',transcribe);monkeypatch.setattr(c,'analyze_segments',analyze)
    return calls

def begin(client,row):
    cid=str(uuid4());response=client.post(f'/api/records/{row["id"]}/captures',json={'id':cid,'expected_revision':row['revision'],'confirmed':True,'started_at':'2026-11-04T10:00:00+08:00'})
    assert response.status_code==201,response.text
    return cid

def put(client,row,cid,n,raw=b'\x01\x00'*80000):
    return client.put(f'/api/records/{row["id"]}/captures/{cid}/chunks/{n}',content=raw,headers={'content-type':'application/octet-stream'})

def finish(client,row,cid,n,partial=False):
    return client.post(f'/api/records/{row["id"]}/captures/{cid}/finish',json={'count':n,'ended_at':'2026-11-04T10:10:00+08:00','partial':partial,'confirmed':partial})

def drain(client):
    engine=client.app.state.engine
    for _ in range(100):
        c.schedule(engine);job=c.claim(engine)
        if job:c.process(engine,*job,client.app.state.store.current)
        else:
            with Session(engine) as s:ids=list(s.scalars(select(c.Capture.id).where(c.Capture.state=='processing')))
            for cid in ids:c.finalize(engine,cid,client.app.state.store.current)
            return
    raise AssertionError('queue did not drain')

def test_continuous_merge_retries_and_protection(client,cloud):
    row=create(client)
    with r.write_session(client.app.state.engine) as session:
        saved=session.get(r.RecordSession,row['id']);saved.template_snapshot={**saved.template_snapshot,'values':{'name':'人工保护值'},'locked_fields':['name']};r.version(session,saved,'updated')
    row=client.get(f'/api/records/{row["id"]}').json()
    cid=begin(client,row)
    for n in range(13):assert put(client,row,cid,n).status_code==200
    assert put(client,row,cid,0).status_code==200
    assert put(client,row,cid,0,b'\x02\x00').status_code==409
    assert client.patch(f'/api/records/{row["id"]}',json={'expected_revision':row['revision'],'title':'不得覆盖'}).status_code==409
    drain(client);assert cloud.count('asr')==1  # ASR starts before recording ends.
    assert finish(client,row,cid,14).status_code==409
    assert finish(client,row,cid,13).status_code==200
    drain(client)
    result=client.get(f'/api/records/{row["id"]}').json()
    assert len(result['audio_clips'])==2 and len(result['segments'])==2
    assert result['template_snapshot']['values']['name']=='人工保护值'
    assert '合成地址1' in result['segments'][0]['text'] and '合成地址2' in result['segments'][1]['text']
    state=client.get(f'/api/records/{row["id"]}/captures').json()['items'][0]
    assert state['state']=='completed' and state['saved_seconds']==65
    before=list(cloud);drain(client);assert cloud==before
    assert finish(client,row,cid,13).status_code==200
    assert client.get(f'/api/records/{row["id"]}/captures/{cid}/backup.wav').content[:4]==b'RIFF'
    # Deleting a generated audio chunk also removes its source PCM and derived candidates.
    audio=result['audio_clips'][0]
    response=client.request('DELETE',f'/api/records/{row["id"]}/audio/{audio["id"]}',json={'expected_revision':result['revision'],'confirmed':True})
    assert response.status_code==200,response.text
    with Session(client.app.state.engine) as s:
        assert not s.scalar(select(c.Batch.id).where(c.Batch.audio_id==audio['id']))
        assert not s.scalar(select(r.RecordFieldCandidate.id).where(r.RecordFieldCandidate.record_id==row['id']))

def test_gap_partial_and_owner_isolation(client,cloud):
    row=create(client);cid=begin(client,row)
    assert put(client,row,cid,0).status_code==200
    assert put(client,row,cid,2).status_code==200
    assert finish(client,row,cid,3).status_code==409
    response=client.get(f'/api/records/{row["id"]}/captures/{cid}/backup.wav',headers={'x-test-user':'another'})
    assert response.status_code==404
    result=finish(client,row,cid,1,True);assert result.status_code==200,result.text
    drain(client)
    result=client.get(f'/api/records/{row["id"]}').json()
    assert len(result['segments'])==1
    assert result['template_snapshot']['capture_incomplete'] is True
    assert '录音不完整' in result['analysis']['issues'][0]

def test_failure_retains_asr_and_lease_recovery(client,cloud,monkeypatch):
    row=create(client);cid=begin(client,row);put(client,row,cid,0);finish(client,row,cid,1)
    engine=client.app.state.engine;c.schedule(engine)
    first=c.claim(engine);assert first and c.claim(engine) is None
    # A dead worker's lease can be reclaimed; stale completion cannot overwrite.
    with r.write_session(engine) as s:s.get(c.Batch,first[0]).lease_until=r.utc_now()-timedelta(seconds=1)
    reclaimed=c.claim(engine);assert reclaimed[1]!=first[1]
    c.process(engine,*first,client.app.state.store.current)
    c.process(engine,*reclaimed,client.app.state.store.current)
    original=c.analyze_segments
    monkeypatch.setattr(c,'analyze_segments',lambda *a:(_ for _ in ()).throw(c.RecordAIError(403,'合成配置错误')))
    drain(client)
    with Session(engine) as s:
        b=s.scalar(select(c.Batch));assert b.state=='failed' and b.text
    assert cloud.count('asr')==1
    monkeypatch.setattr(c,'analyze_segments',original)
    result=client.post(f'/api/records/{row["id"]}/captures/{cid}/retry',json={'expected_revision':row['revision'],'confirmed':True})
    assert result.status_code==200,result.text
    drain(client);assert cloud.count('asr')==1
    assert client.get(f'/api/records/{row["id"]}/captures').json()['items'][0]['state']=='completed'


def test_disk_full_and_invalid_chunks_are_not_acknowledged(client,monkeypatch):
    row=create(client);cid=begin(client,row)
    assert put(client,row,cid,-1).status_code==422
    assert put(client,row,cid,0,b'odd').status_code==422
    monkeypatch.setattr(c.shutil,'disk_usage',lambda _:SimpleNamespace(free=1024))
    assert put(client,row,cid,0).status_code==507
    with Session(client.app.state.engine) as s:assert s.scalar(select(func.count()).select_from(c.Chunk))==0


def test_skip_failed_audio_is_explicit_and_original_is_retained(client,cloud,monkeypatch):
    row=create(client);cid=begin(client,row);put(client,row,cid,0);finish(client,row,cid,1)
    monkeypatch.setattr(c,'transcribe_audio',lambda *a:(_ for _ in ()).throw(c.RecordAIError(403,'合成服务配置错误')))
    drain(client)
    with Session(client.app.state.engine) as s:b=s.scalar(select(c.Batch));bid=b.id;assert b.state=='failed'
    path=f'/api/records/{row["id"]}/captures/{cid}/batches/{bid}/skip'
    assert client.post(path,json={'expected_revision':row['revision'],'confirmed':False}).status_code==422
    assert client.post(path,json={'expected_revision':row['revision'],'confirmed':True}).status_code==200
    drain(client)
    state=client.get(f'/api/records/{row["id"]}/captures').json()['items'][0]
    assert state['state']=='partial' and '未获得可用转写' in state['error']
    result=client.get(f'/api/records/{row["id"]}').json()
    assert len(result['audio_clips'])==1 and result['segments']==[]


def test_long_capture_and_completed_results_reused(client,cloud):
    row=create(client);cid=begin(client,row)
    for n in range(73):assert put(client,row,cid,n).status_code==200
    assert finish(client,row,cid,73).status_code==200
    # Six minutes of saved PCM are processed without the old five minute stop.
    drain(client)
    state=client.get(f'/api/records/{row["id"]}/captures').json()['items'][0]
    assert state['state']=='completed' and state['saved_seconds']==365
    result=client.get(f'/api/records/{row["id"]}').json()
    assert sum(a['duration_ms'] for a in result['audio_clips'])==365000
    old_calls=cloud.count('asr');cid2=begin(client,result);put(client,result,cid2,0);finish(client,result,cid2,1)
    drain(client);assert cloud.count('asr')==old_calls+1
    newer=client.get(f'/api/records/{row["id"]}').json()
    assert len(newer['segments'])==len(result['segments'])+1


def test_busy_speech_at_sixty_seconds_waits_for_pause_without_losing_chunks(client):
    row=create(client);cid=begin(client,row)
    loud=b'\x00\x10'*80000
    for i in range(12):assert put(client,row,cid,i,loud).status_code==200
    c.schedule(client.app.state.engine)
    with Session(client.app.state.engine) as s:assert not s.scalars(select(c.Batch)).all()
    assert put(client,row,cid,12,b'\x00\x00'*80000).status_code==200
    c.schedule(client.app.state.engine)
    with Session(client.app.state.engine) as s:
        batch=s.scalar(select(c.Batch));assert batch.first==0 and batch.last==12 and batch.samples==65*16000


def test_capture_reuses_speaker_count_for_audio_processing(client,cloud,monkeypatch):
    from backend.app import record_voice_audio
    monkeypatch.setattr(record_voice_audio,'available',lambda:True)
    counts=[]
    def transcribe(raw,fmt,mime,settings,*,speaker_count):
        counts.append(speaker_count)
        return {'text':'它是谁的？'}
    monkeypatch.setattr(c,'transcribe_audio',transcribe)
    rec=create(client)
    cid=str(uuid4())
    path=f'/api/records/{rec["id"]}/captures'
    payload={'id':cid,'expected_revision':rec['revision'],'confirmed':True,
             'started_at':'2026-11-04T10:00:00+08:00','speaker_count':3}
    assert client.post(path,json=payload).status_code==201
    assert client.post(path,json=payload).status_code==201
    assert client.post(path,json={**payload,'speaker_count':2}).status_code==409
    assert put(client,rec,cid,0).is_success
    assert finish(client,rec,cid,1).is_success
    engine=client.app.state.engine
    c.schedule(engine)
    c.process(engine,*c.claim(engine),client.app.state.store.current)
    assert counts==[3]
