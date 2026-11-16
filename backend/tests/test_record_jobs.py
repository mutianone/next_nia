"""Synthetic acceptance: checkpoints, conflicts, manual protection and erasure."""
from types import SimpleNamespace
import json
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.tests.test_records import client, cloud, enable_ai, with_segments, segments, editable, upload
from backend.app import record_jobs as jobs
from backend.app import records as r


@pytest.fixture
def queue(client,monkeypatch):
    monkeypatch.setattr('backend.app.record_cloud.reserve',lambda:None)
    monkeypatch.setattr('backend.app.record_cloud.time.sleep',lambda _:None)
    tasks = []
    client.app.state.record_executor = SimpleNamespace(submit=lambda fn, *args: tasks.append((fn, args)))
    return tasks


def start(client, row):
    result = client.post(f"/api/records/{row['id']}/generation-jobs", json={'expected_revision':row['revision'],'confirmed':True})
    assert result.status_code == 202, result.text
    return result.json()


def current(client, row):
    return client.get(f"/api/records/{row['id']}").json()


def response(request):
    data = json.loads(json.loads(request.read())['messages'][1]['content'])
    field = data['template_fields'][0]['id']
    return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
        'summary':'合成摘要','key_facts':[{'label':field,'value':'合成内容','segment_ids':[data['source_ids'][0]]}],'issues':[]})}}]})


def run(queue):
    fn, args = queue.pop(0)
    fn(*args)


def test_failure_resume_only_unfinished_groups(client,cloud,queue):
    enable_ai(client,'deepseek'); row=with_segments(client,template_id='bride')
    job=start(client,row)
    assert job['total']==5 and current(client,row)['generation_job']['status']=='queued'
    # Duplicate submission returns same task rather than sending another request.
    assert start(client,row)['id']==job['id'] and len(queue)==1
    cloud.respond=lambda request: httpx.Response(401) if len(cloud.calls)==2 else response(request)
    run(queue)
    actual=current(client,row)
    assert actual['generation_job']['status']=='failed'
    assert actual['generation_job']['completed']==1
    assert not actual['template_snapshot'].get('generated')
    cloud.respond=response
    result=client.post(f"/api/records/{row['id']}/generation-jobs/{job['id']}/resume",json={'expected_revision':row['revision'],'confirmed':True})
    assert result.status_code==202,result.text
    run(queue)
    actual=current(client,row)
    assert actual['generation_job']['status']=='succeeded'
    assert len(cloud.calls)==job['total']+1  # Successful groups plus exactly 1 failed call
    assert actual['revision']==row['revision']+1
    assert actual['template_snapshot']['generated']
    assert client.get(f"/api/records/{row['id']}/export.docx").status_code==200


def test_stale_job_never_overwrites_edit(client,cloud,queue):
    enable_ai(client,'deepseek'); row=with_segments(client,template_id='bride'); start(client,row)
    updated=segments(client,row,[editable(row['segments'][0],text='新的人工陈述')]).json()
    cloud.respond=response; run(queue)
    actual=current(client,row)
    assert actual['generation_job']['status']=='stale'
    assert actual['revision']==updated['revision'] and not actual['template_snapshot'].get('generated')
    assert cloud.calls==[]


def test_manual_field_and_blank_preserved_until_unlock(client,cloud,queue):
    enable_ai(client,'deepseek'); row=with_segments(client,template_id='bride');cloud.respond=response
    start(client,row);run(queue);row=current(client,row)
    key=next(iter(row['template_snapshot']['values']))
    row=client.patch(f"/api/records/{row['id']}/template-values",json={'expected_revision':row['revision'],'values':{key:''}}).json()
    assert key in row['template_snapshot']['locked_fields']
    start(client,row);run(queue);row=current(client,row)
    assert row['template_snapshot']['values'][key]==''
    assert key not in row['template_snapshot']['sources']
    row=client.patch(f"/api/records/{row['id']}/template-values",json={'expected_revision':row['revision'],'values':{},'unlock_fields':[key]}).json()
    start(client,row);run(queue);row=current(client,row)
    assert row['template_snapshot']['values'][key]=='合成内容'


def test_restart_requires_confirmation_and_preserves_checkpoints(client,cloud,queue):
    enable_ai(client,'deepseek');row=with_segments(client,template_id='bride');job=start(client,row)
    r.recover_interrupted_attempts(client.app.state.engine);jobs.recover(client.app.state.engine)
    assert current(client,row)['generation_job']['status']=='interrupted'
    run(queue);assert cloud.calls==[]
    path=f"/api/records/{row['id']}/generation-jobs/{job['id']}/resume"
    assert client.post(path,json={'expected_revision':row['revision'],'confirmed':False}).status_code==400
    assert client.post(path,json={'expected_revision':row['revision'],'confirmed':True}).status_code==202
    cloud.respond=response;run(queue)
    assert current(client,row)['generation_job']['status']=='succeeded'


def test_delete_audio_purges_checkpoint_inputs_outputs(client,cloud,queue):
    enable_ai(client,'deepseek');row=with_segments(client,template_id='bride')
    row=upload(client,row).json();audio=row['audio_clips'][0]['id']
    row=segments(client,row,[editable(row['segments'][0],audio_id=audio)]).json()
    start(client,row)
    path=f"/api/records/{row['id']}/audio/{audio}"
    assert client.request('DELETE',path,json={'expected_revision':row['revision'],'confirmed':True}).status_code==409
    cloud.respond=response;run(queue);row=current(client,row)
    result=client.request('DELETE',path,json={'expected_revision':row['revision'],'confirmed':True})
    assert result.status_code==200,result.text
    assert result.json()['generation_job'] is None and result.json()['segments']==[]
    with Session(client.app.state.engine) as session:
        assert session.scalar(select(jobs.GenerationJob).where(jobs.GenerationJob.record_id==row['id'])) is None


def test_invalid_reference_not_checkpointed(client,cloud,queue):
    enable_ai(client,'deepseek');row=with_segments(client,template_id='bride');start(client,row)
    def invalid(request):
        result=response(request).json()
        data=json.loads(result['choices'][0]['message']['content'])
        data['key_facts'][0]['segment_ids']=['invented']
        result['choices'][0]['message']['content']=json.dumps(data)
        return httpx.Response(200,json=result)
    cloud.respond=invalid;run(queue)
    actual=current(client,row)
    assert actual['generation_job']['completed']==0
    assert 'invalid_reference' in actual['generation_job']['error']


def test_acceptance_samples_only_assert_real_template_fields():
    from pathlib import Path
    from backend.app.record_templates import CASE_TEMPLATES
    samples=json.loads((Path(__file__).parent/'fixtures/record_acceptance/samples.json').read_text())
    assert {s['template_id'] for s in samples}=={'bride','bride_family','border_case'}
    for sample in samples:
        template=next(t for t in CASE_TEMPLATES if t['id']==sample['template_id'])
        assert set(sample['checks']) <= {f['id'] for f in template['fields']}
        assert '虚构' in sample['text']


def test_identical_input_reuses_valid_result(client,cloud,queue):
    enable_ai(client,'deepseek');row=with_segments(client,template_id='bride')
    cloud.respond=lambda request: __import__('httpx').Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"summary":"没有可靠回答","key_facts":[],"issues":[]}'}}]})
    first=start(client,row);run(queue);row=current(client,row)
    calls=len(cloud.calls)
    second=start(client,row)
    assert second['id']==first['id'] and second['reused']
    assert len(cloud.calls)==calls and queue==[]
