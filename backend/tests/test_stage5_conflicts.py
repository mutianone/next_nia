from copy import deepcopy
import pytest
from backend.tests.test_record_capture import client
from backend.tests.test_fill_apply import ready,request_body,apply
from backend.tests.test_qa_drafts import get,url
from backend.app import records as r
from backend.app.records_ai import RecordAIError
from backend.app.record_jobs import unchanged
from types import SimpleNamespace


def test_legacy_auto_preserves_structured_and_human_locked_fields(client):
    row,b=ready(client);assert apply(client,row,request_body(client,row,b)).status_code==200
    current=get(client,row,'');t=current['template_snapshot'];old=deepcopy(t)
    output=r.filled_template(t,[{'label':'q_name','value':'错误覆盖','segment_ids':['fake']}],t['source_hash'])
    assert output['values']==old['values'] and output['structured_fill_sources']==old['structured_fill_sources']
    assert t==old and not output['sources']
    stale=r.filled_template(t,[],'changed-source')
    assert stale['source_hash']==t['source_hash'] and stale['values']==t['values']


def test_human_edit_clears_old_structured_attribution_and_blocks_restore(client):
    row,b=ready(client);assert apply(client,row,request_body(client,row,b)).status_code==200
    current=get(client,row,'')
    response=client.patch(url(row,'template-values'),json={'expected_revision':current['revision'],'values':{'q_name':'人工核对姓名'}})
    assert response.status_code==200
    t=response.json()['template_snapshot'];assert 'q_name' not in t['structured_fill_sources'] and 'q_name' in t['locked_fields']
    assert get(client,row,'fill-apply/history')['items'][0]['restore_preview'] is None


def test_pending_legacy_job_cannot_commit_after_structured_apply(client):
    row,b=ready(client);before=get(client,row,'')
    with r.write_session(client.app.state.engine) as s:
        rec=s.get(r.RecordSession,row['id']);attempt=SimpleNamespace(input_revision=rec.revision,input_hash=r.segment_hash(r.ordered_segments(s,rec.id)))
        assert unchanged(s,rec,attempt)
    assert apply(client,row,request_body(client,row,b)).status_code==200
    with r.write_session(client.app.state.engine) as s:assert not unchanged(s,s.get(r.RecordSession,row['id']),attempt)


def test_audio_delete_cannot_erase_formal_history(client):
    row,b=ready(client);assert apply(client,row,request_body(client,row,b)).status_code==200
    current=get(client,row,'');response=client.request('DELETE',url(row,'audio/synthetic'),json={'expected_revision':current['revision'],'confirmed':True})
    assert response.status_code==409 and len(get(client,row,'fill-apply/history')['items'])==1


def test_old_candidate_cannot_overwrite_reviewed_formal_value(client):
    from fastapi import HTTPException
    row,b=ready(client);assert apply(client,row,request_body(client,row,b)).status_code==200
    before=get(client,row,'')
    with pytest.raises(HTTPException) as conflict:
        with r.write_session(client.app.state.engine) as s:
            rec=s.get(r.RecordSession,row['id']);t=deepcopy(rec.template_snapshot);t['values']['q_name']='旧候选错误覆盖';rec.template_snapshot=t
            r.version(s,rec,'candidate_confirmed')
    assert conflict.value.status_code==409 and get(client,row,'')['template_snapshot']==before['template_snapshot']


@pytest.mark.parametrize('operation',['apply','restore'])
def test_sqlite_human_edit_races(client,operation):
    from backend.tests.test_stage5_postgres import test_human_edit_race
    test_human_edit_race(client,operation)


def test_sqlite_different_candidate_race(client):
    from backend.tests.test_stage5_postgres import test_different_candidates_concurrent
    test_different_candidates_concurrent(client)
