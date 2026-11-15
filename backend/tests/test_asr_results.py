"""Stage-one acceptance: synthetic audio/provider fixtures only, no network."""
from copy import deepcopy
from importlib import import_module
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, select, func, inspect
from sqlalchemy.orm import Session
from backend.app import record_asr_results as ar, records as r, record_capture as c
from backend.app.record_qa_review import validate_turns
from backend.app.records_ai import RecordAIError
from backend.tests.test_record_capture import client, begin, put, finish, drain
from backend.tests.test_records import create, enable_ai, upload, segments, editable


def output(task='task-1', speakers=(0, 1), texts=('你叫什么名字？', '张某。')):
    return ar.normalize_funasr({'transcripts':[{'channel_id':0,'sentences':[
        {'sentence_id':i+1,'speaker_id':speaker,'begin_time':i*1000,'end_time':i*1000+900,'text':text,
         'words':[{'begin_time':i*1000,'end_time':i*1000+900,'text':text,'punctuation':''}]}
        for i,(speaker,text) in enumerate(zip(speakers,texts))]}]},task)


@pytest.mark.parametrize('speakers', [(0,0),(0,1),(None,1)])
def test_normalizer_preserves_sentences_and_words(speakers):
    result=output(speakers=speakers)
    assert len(result['segments'])==2
    for i, part in enumerate(result['segments']):
        assert part['speaker_id']==speakers[i]
        assert part['speaker']=='unknown' and '[说话人' not in part['text']
        assert part['sentence_id']==i+1 and part['channel_id']==0
        assert part['words'][0]['begin_time']==i*1000
        assert part['timestamp_unit']=='ms' and part['timestamp_source']=='PROVIDER'
    assert result['raw_result']['transcripts'][0]['sentences'][0]['text']==result['segments'][0]['text']


def test_all_tracks_empty_words_missing_speaker_and_times():
    data=output()['raw_result'];data['transcripts'][0]['sentences'][0]['words']=[]
    del data['transcripts'][0]['sentences'][0]['speaker_id']
    data['transcripts'].append({'channel_id':1,'sentences':[{'text':'第三人。','words':[]}]})
    result=ar.normalize_funasr(data,'tracks')
    assert len(result['segments'])==3
    assert result['segments'][0]['words']==[] and result['segments'][0]['speaker_id'] is None
    assert result['segments'][2]['start_ms'] is None and result['segments'][2]['channel_id']==1


@pytest.mark.parametrize('change',[{'begin_time':-1},{'end_time':0},{'end_time':None},{'begin_time':True},
    {'words':[{'text':'假','begin_time':0,'end_time':9999}]}])
def test_invalid_times_rejected_without_invention(change):
    data=output()['raw_result'];data['transcripts'][0]['sentences'][0].update(change)
    with pytest.raises(RecordAIError):ar.normalize_funasr(data,'invalid')


def test_multi_interval_gap_and_word_mapping():
    mapping=[{'batch_start_sample':0,'batch_end_sample':16000,'session_start_sample':0,'sample_rate':16000},
             {'batch_start_sample':16000,'batch_end_sample':32000,'session_start_sample':48000,'sample_rate':16000}]
    spans=ar.map_interval(500,1500,mapping)
    assert [(s['start_ms'],s['end_ms']) for s in spans]==[(500,1000),(3000,3500)]
    assert ar.map_interval(None,None,mapping)==[]


def test_immutable_versions_idempotency_manual_protection_scope(client,monkeypatch):
    row=create(client);enable_ai(client)
    row=upload(client,row).json();audio_id=row['audio_clips'][0]['id']
    state={'result':output()}
    monkeypatch.setattr(r,'transcribe_audio',lambda *a,**kw:deepcopy(state['result']))
    def transcribe(row):
        res=client.post(f"/api/records/{row['id']}/audio/{audio_id}/transcribe",json={'expected_revision':row['revision'],'confirmed':True})
        assert res.status_code==200,res.text
        return res.json()
    first=transcribe(row)
    assert len(first['segments'])==2
    again=transcribe(first)
    assert [s['id'] for s in again['segments']]==[s['id'] for s in first['segments']]
    revised=segments(client,again,[editable(s,text='人工确认。',speaker='interviewee') for s in again['segments']]).json()
    state['result']=output(task='task-2',texts=('不同问题？','新答案。'))
    latest=transcribe(revised)
    assert [s['text'] for s in latest['segments']]==['人工确认。','人工确认。']
    versions=client.get(f"/api/records/{row['id']}/recognition-runs").json()['items']
    assert len(versions)==2 and sum(v['applied'] for v in versions)==1
    assert versions[0]['sentences'][0]['speaker_scope']!=versions[1]['sentences'][0]['speaker_scope']
    assert {v['sentences'][0]['text'] for v in versions}=={'你叫什么名字？','不同问题？'}
    with Session(client.app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(ar.RecognitionSentence))==4
        run=session.get(ar.RecognitionRun,versions[0]['id'])
        assert run.raw_result['transcripts'][0]['sentences'][1]['words']
    # Existing audio-delete semantics must also purge new source metadata.
    deleted=client.request('DELETE',f"/api/records/{row['id']}/audio/{audio_id}",json={'expected_revision':latest['revision'],'confirmed':True})
    assert deleted.status_code==200,deleted.text
    with Session(client.app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(ar.RecognitionRun))==0
        assert session.scalar(select(func.count()).select_from(ar.RecognitionSentence))==0


def test_same_run_different_payload_rejected(client):
    row=create(client)
    with r.write_session(client.app.state.engine) as s:
        first=ar.save_result(s,row['id'],'audio:x',output())
        assert ar.save_result(s,row['id'],'audio:x',output()).id==first.id
        with pytest.raises(RecordAIError):ar.save_result(s,row['id'],'audio:x',output(texts=('变化？','不同。')))


def test_continuous_two_batches_use_sentence_ids_and_timeline(client,monkeypatch):
    row=create(client);cid=begin(client,row);calls=[];observed=[]
    monkeypatch.setattr(c,'BATCH_SAMPLES',160000)
    def recognize(*args):
        calls.append(1);return output(task=f'batch-{len(calls)}')
    def analyze(inputs,settings,template):
        observed.extend(inputs)
        return {'summary':'虚构测试','key_facts':[{'label':template['fields'][0]['id'],'value':inputs[-1]['text'],'segment_ids':[inputs[-1]['segment_id']]}],'issues':[]}
    monkeypatch.setattr(c,'transcribe_audio',recognize);monkeypatch.setattr(c,'analyze_segments',analyze)
    for n in range(3):assert put(client,row,cid,n).status_code==200
    assert finish(client,row,cid,3).status_code==200
    drain(client)
    result=client.get(f"/api/records/{row['id']}").json()
    assert len(calls)==2 and len(result['segments'])==4
    assert [s['text'] for s in result['segments']]==['你叫什么名字？','张某。']*2
    assert [s['start_ms'] for s in result['segments']]==[0,1000,0,1000]
    assert [s['session_intervals'][0]['start_ms'] for s in result['segments']]==[0,1000,10000,11000]
    assert all(s['words'] and s['sentence_id'] for s in result['segments'])
    ids={s['id'] for s in result['segments']}
    assert all(s['segment_id'] in ids for s in observed)
    assert all(set(f['segment_ids'])<=ids for f in result['analysis']['key_facts'])
    assert result['segments'][0]['speaker_scope']!=result['segments'][2]['speaker_scope']
    before=[s['id'] for s in result['segments']];drain(client)
    assert [s['id'] for s in client.get(f"/api/records/{row['id']}").json()['segments']]==before


def test_overlapping_windows_retained_but_not_duplicated(client):
    row=create(client);row=upload(client,row).json();audio=row['audio_clips'][0]['id']
    with r.write_session(client.app.state.engine) as s:
        record=s.get(r.RecordSession,row['id'])
        mapping=[{'batch_start_sample':0,'batch_end_sample':32000,'session_start_sample':0,'sample_rate':16000}]
        first=ar.save_result(s,row['id'],'capture:session:one',output(),audio,mapping)
        assert ar.project(s,first,record,audio,'attempt',0)==2
        second=ar.save_result(s,row['id'],'capture:session:two',output(task='overlap'),None,mapping)
        # Separate saved audio for the overlapping window.
        new_audio=r.AudioClip(record_id=row['id'],filename='synthetic.wav',format='wav',mime_type='audio/wav',size_bytes=2,sha256='test',raw_audio=b'00')
        s.add(new_audio);s.flush()
        assert ar.project(s,second,record,new_audio.id,'attempt-2',2)==0
        entries=ar.sentences(s,second)
        assert all(e.projection['status']=='overlap_review_required' and e.projection['overlaps'] for e in entries)
        assert s.scalar(select(func.count()).select_from(r.RecordSegment))==2


def test_manual_mapping_uses_run_scope_not_text(client):
    sources=[]
    for run in ('run-1','run-2'):
        sources.append({'id':run,'recognition_run_id':run,'speaker_id':0,'text':'测试。','audio_id':'audio','start_ms':0,'end_ms':100})
    turns,_=validate_turns({'turns':[{'segment_id':s['id'],'text':s['text'],'suggested_role':'unknown'} for s in sources]},sources)
    assert [t['voice_group'] for t in turns]==['run-1:0','run-2:0']
    assert all(t['speaker']=='unknown' for t in turns)


def test_migration_idempotent_and_legacy_read(client,tmp_path):
    migration=import_module('backend.migrations.20261115_asr_results')
    engine=create_engine(f'sqlite:///{tmp_path}/migration.sqlite3')
    with engine.begin() as conn:conn.exec_driver_sql('CREATE TABLE legacy (id INTEGER PRIMARY KEY, text TEXT)');conn.exec_driver_sql("INSERT INTO legacy VALUES (1,'原记录')")
    migration.upgrade(engine);migration.upgrade(engine)
    assert {'record_recognition_runs','record_recognition_sentences'}<=set(inspect(engine).get_table_names())
    with engine.connect() as conn:assert conn.exec_driver_sql('SELECT text FROM legacy').scalar()=='原记录'
    engine.dispose()
    row=create(client)
    revised=segments(client,row,[{'text':'历史记录。','speaker':'unknown'}]).json()['segments'][0]
    assert 'recognition_run_id' not in revised and revised['start_ms'] is None


def test_large_result_saved_without_truncating_provider_sentences():
    raw={'transcripts':[{'channel_id':0,'sentences':[{'sentence_id':i,'speaker_id':None,'text':'测试。','begin_time':i*10,'end_time':i*10+5,'words':[]} for i in range(501)]}]}
    assert len(ar.normalize_funasr(raw,'long')['segments'])==501


def test_sample_map_saved_without_crossing_gap(client):
    row=create(client)
    mapping=[{'batch_start_sample':0,'batch_end_sample':8000,'session_start_sample':0,'sample_rate':16000},
             {'batch_start_sample':8000,'batch_end_sample':32000,'session_start_sample':24000,'sample_rate':16000}]
    with r.write_session(client.app.state.engine) as s:
        run=ar.save_result(s,row['id'],'capture:gap:batch',output(),intervals=mapping)
        first=ar.sentences(s,run)[0].data
        assert len(first['session_intervals'])==2 and len(first['word_session_intervals'][0])==2
        assert first['start_ms']==0 and first['end_ms']==900
        assert first['session_intervals'][1]['start_ms']==1500


def test_missing_time_window_overlap_is_conservative(client):
    row=create(client);row=upload(client,row).json();audio=row['audio_clips'][0]['id']
    raw={'transcripts':[{'channel_id':0,'sentences':[{'text':'未知时间原文。','speaker_id':None}]}]}
    mapping=[{'batch_start_sample':0,'batch_end_sample':16000,'session_start_sample':0,'sample_rate':16000}]
    with r.write_session(client.app.state.engine) as s:
        record=s.get(r.RecordSession,row['id'])
        first=ar.save_result(s,row['id'],'capture:s:a',ar.normalize_funasr(raw,'one'),audio,mapping)
        assert ar.project(s,first,record,audio,'a',0)==1
        second=ar.save_result(s,row['id'],'capture:s:b',ar.normalize_funasr(raw,'two'),None,mapping)
        assert ar.project(s,second,record,'another-audio','b',1)==0
        entry=ar.sentences(s,second)[0]
        assert entry.projection['status']=='overlap_review_required' and entry.data['start_ms'] is None


def test_changed_final_task_response_returns_conflict_without_stuck_attempt(client,monkeypatch):
    row=create(client);enable_ai(client);row=upload(client,row).json();audio=row['audio_clips'][0]['id']
    monkeypatch.setattr(r,'transcribe_audio',lambda *a,**kw:output())
    path=f"/api/records/{row['id']}/audio/{audio}/transcribe"
    row=client.post(path,json={'expected_revision':row['revision'],'confirmed':True}).json()
    monkeypatch.setattr(r,'transcribe_audio',lambda *a,**kw:output(texts=('变了？','变了。')))
    result=client.post(path,json={'expected_revision':row['revision'],'confirmed':True})
    assert result.status_code==409
    current=client.get(f"/api/records/{row['id']}").json()
    assert len(current['segments'])==2 and all(a['status']!='processing' for a in current['transcription_attempts'])


@pytest.mark.parametrize('template_id',['bride','bride_family','border_case'])
def test_new_sentence_metadata_does_not_change_word_export(client,monkeypatch,template_id):
    from io import BytesIO
    from zipfile import ZipFile
    from xml.etree import ElementTree as ET
    row=create(client,template_id=template_id);enable_ai(client);row=upload(client,row).json()
    monkeypatch.setattr(r,'transcribe_audio',lambda *a,**kw:output())
    path=f"/api/records/{row['id']}"
    row=client.post(path+f"/audio/{row['audio_clips'][0]['id']}/transcribe",json={'expected_revision':row['revision'],'confirmed':True}).json()
    # Validated-field fixture tests the existing renderer, not AI accuracy.
    with r.write_session(client.app.state.engine) as s:
        record=s.get(r.RecordSession,row['id'])
        record.template_snapshot=r.filled_template(record.template_snapshot,[{'label':'name','value':'张某','segment_ids':[row['segments'][1]['id']]}],r.segment_hash(r.ordered_segments(s,row['id'])))
        r.version(s,record,'synthetic_export_fixture')
    response=client.get(path+'/export.docx')
    assert response.status_code==200,response.text
    with ZipFile(BytesIO(response.content)) as archive:
        text=''.join(ET.fromstring(archive.read('word/document.xml')).itertext())
        assert '张某' in text and '[说话人' not in text
