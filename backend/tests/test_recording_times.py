from backend.tests.test_records import client, cloud, enable_ai, with_segments, analysis_response, MP3


def record_audio(client, row, start=None, end=None):
    data={'expected_revision':str(row['revision'])}
    if start is not None:data['recorded_started_at']=start
    if end is not None:data['recorded_ended_at']=end
    return client.post(f"/api/records/{row['id']}/audio",data=data,files={'file':('recording.mp3',MP3,'audio/mpeg')})


def test_system_times_override_ai_and_export(client,cloud):
    enable_ai(client,'deepseek');row=with_segments(client,template_id='bride')
    row=record_audio(client,row,'2026-11-02T15:59:00Z','2026-11-02T16:04:00Z').json()
    assert row['occurred_at'].startswith('2026-11-02T15:59:00')
    cloud.respond=lambda request:analysis_response(row['segments'][0]['id'],key_facts=[{'label':'start_year','value':'2000','segment_ids':[row['segments'][0]['id']]}])
    response=client.post(f"/api/records/{row['id']}/analyze",json={'expected_revision':row['revision'],'confirmed':True})
    assert response.status_code==200,response.text
    row=response.json();v=row['template_snapshot']['values']
    assert [v[k] for k in ('start_year','start_month','start_day','start_hour','start_minute')]==['2026','11','2','23','59']
    assert [v[k] for k in ('end_year','end_month','end_day','end_hour','end_minute')]==['2026','11','3','0','4']
    assert 'start_year' not in row['template_snapshot']['sources']
    from zipfile import ZipFile
    from io import BytesIO
    from xml.etree import ElementTree as ET
    exported=client.get(f"/api/records/{row['id']}/export.docx")
    assert exported.status_code==200
    with ZipFile(BytesIO(exported.content)) as archive:
        text=''.join(ET.fromstring(archive.read('word/document.xml')).itertext())
        assert '2026' in text and '2000' not in text


def test_multiple_clips_and_delete_recalculate_times(client):
    row=with_segments(client,template_id='bride')
    row=record_audio(client,row,'2026-11-03T02:10:00Z','2026-11-03T02:15:00Z').json()
    later=row['audio_clips'][0]['id']
    row=record_audio(client,row,'2026-11-03T02:00:00Z','2026-11-03T02:05:00Z').json()
    assert row['template_snapshot']['values']['start_minute']=='0'
    assert row['template_snapshot']['values']['end_minute']=='15'
    row=client.request('DELETE',f"/api/records/{row['id']}/audio/{later}",json={'expected_revision':row['revision'],'confirmed':True}).json()
    assert row['template_snapshot']['values']['end_minute']=='5'
    first=row['audio_clips'][0]['id']
    row=client.request('DELETE',f"/api/records/{row['id']}/audio/{first}",json={'expected_revision':row['revision'],'confirmed':True}).json()
    assert row['occurred_at'] is None
    assert not row['template_snapshot'].get('values')


def test_uploaded_audio_does_not_invent_recording_time(client):
    row=with_segments(client,template_id='bride')
    row=record_audio(client,row).json()
    assert row['occurred_at'] is None
    assert not row['template_snapshot'].get('time_source')


def test_invalid_or_incomplete_time_rejected(client):
    row=with_segments(client,template_id='bride')
    assert record_audio(client,row,'2026-11-03T02:00:00Z').status_code==422
    assert record_audio(client,row,'2026-11-03T02:00:00','2026-11-03T02:01:00').status_code==422
    assert record_audio(client,row,'2026-11-03T02:01:00Z','2026-11-03T02:00:00Z').status_code==422


def test_uploaded_audio_rejects_invented_ai_times_and_location(client, cloud):
    enable_ai(client, 'deepseek')
    row = with_segments(client, template_id='bride')
    sid = row['segments'][0]['id']
    cloud.respond = lambda request: analysis_response(sid, key_facts=[
        {'label': 'start_year', 'value': '2023', 'segment_ids': [sid]},
        {'label': 'end_hour', 'value': '11', 'segment_ids': [sid]},
        {'label': 'location', 'value': '被询问人的居住地址', 'segment_ids': [sid]},
        {'label': 'name', 'value': '测试人', 'segment_ids': [sid]},
    ])
    response = client.post(f"/api/records/{row['id']}/analyze", json={'expected_revision': row['revision'], 'confirmed': True})
    assert response.status_code == 200, response.text
    template = response.json()['template_snapshot']
    assert 'start_year' not in template['values']
    assert 'end_hour' not in template['values']
    assert template['values'].get('location', '') == row['location']
    assert template['values']['name'] == '测试人'
    assert 'location' not in template['sources']


def test_extraction_uses_metadata_for_time_and_location():
    from backend.app.record_templates import CASE_TEMPLATES, extraction_groups, SYSTEM_FIELDS
    fields = [f['id'] for group in extraction_groups(CASE_TEMPLATES[0]) for f in group]
    assert not SYSTEM_FIELDS.intersection(fields)
    assert 'name' in fields and 'q001' in fields
