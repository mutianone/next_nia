"""Synthetic tests: candidate provenance, protection, review and erasure."""
from types import SimpleNamespace
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from backend.tests.test_records import client, cloud, enable_ai, with_segments, segments, editable, upload
from backend.tests.test_record_jobs import queue, start, run, current, response
from backend.app import records as r
from backend.app import record_candidates as c


def test_qa_exact_offsets_and_followups():
    text='问：你几点到的？\n答：晚上八点左右。\n问：你刚才说的八点，是昨天晚上吗？\n答：对，昨天晚上八点。'
    template={'fields':[{'id':'q1','label':'到达时间','question_aliases':['你几点到的？']}]}
    pairs=c.pair_qa([SimpleNamespace(id='s1',text=text,speaker='unknown')],template)
    assert len(pairs)==2 and pairs[1]['possible_followup_of']==0
    assert '晚上八点' in pairs[0]['answer'] and '昨天晚上八点' in pairs[1]['answer']
    for pair in pairs:
        assert text[pair['sources'][0]['start']:pair['sources'][0]['end']]==pair['question']
        assert text[pair['sources'][1]['start']:pair['sources'][1]['end']]==pair['answer']
    assert c.rule_facts(pairs)==[]  # Follow-up requires contextual cloud/manual handling.
    assert c.rule_facts(pairs[:1])[0]['value']==pairs[0]['answer']
    assert c.pair_qa([SimpleNamespace(id='s1',text='昨天下午到达。',speaker='unknown')],template)==[]


def generated(client,cloud,queue):
    enable_ai(client,'deepseek');row=with_segments(client,template_id='bride')
    cloud.respond=response;start(client,row);run(queue)
    result=current(client,row)
    assert result['generation_job']['status']=='succeeded'
    return result


def review(client,row,candidate,decision,**extra):
    return client.post(f"/api/records/{row['id']}/candidates/{candidate['id']}/review",json={
        'expected_revision':row['revision'],'decision':decision,**extra})


def test_confirm_protect_regenerate_history_and_evidence(client,cloud,queue):
    row=generated(client,cloud,queue);candidate=row['candidates'][0];key=candidate['field_id']
    assert candidate['status']=='pending'
    evidence=client.get(f"/api/records/{row['id']}/candidates/{candidate['id']}/evidence").json()
    assert evidence['sources'][0]['quote']==row['segments'][0]['text']
    assert evidence['sources'][0]['precision']=='segment'
    result=review(client,row,candidate,'confirmed',note='合成测试核对');assert result.status_code==200,result.text
    previous_revision=row['revision'];row=result.json();assert row['revision']==previous_revision+1
    assert key in row['template_snapshot']['locked_fields']
    again=review(client,row,candidate,'confirmed');assert again.status_code==200
    assert again.json()['revision']==row['revision']
    start(client,row);run(queue);row=current(client,row)
    field_versions=[x for x in row['candidates'] if x['field_id']==key]
    assert len(field_versions)==2 and sum(x['latest'] for x in field_versions)==1
    assert any(x['status']=='confirmed' and not x['latest'] for x in field_versions)


def test_conflict_requires_explicit_replace_or_reject(client,cloud,queue):
    row=generated(client,cloud,queue);candidate=row['candidates'][0];key=candidate['field_id']
    row=client.patch(f"/api/records/{row['id']}/template-values",json={'expected_revision':row['revision'],'values':{key:'人工确认原值'}}).json()
    start(client,row);run(queue);row=current(client,row)
    candidate=next(c for c in row['candidates'] if c['latest'] and c['field_id']==key)
    assert candidate['status']=='conflict' and row['template_snapshot']['values'][key]=='人工确认原值'
    assert review(client,row,candidate,'confirmed').status_code==409
    assert client.patch(f"/api/records/{row['id']}",json={'expected_revision':row['revision'],'status':'reviewed','confirmed':True,'review_note':'核对'}).status_code==422
    result=review(client,row,candidate,'rejected');assert result.status_code==200,result.text
    row=result.json();assert row['template_snapshot']['values'][key]=='人工确认原值'
    reviewed=client.patch(f"/api/records/{row['id']}",json={'expected_revision':row['revision'],'status':'reviewed','confirmed':True,'review_note':'核对'})
    assert reviewed.status_code==200,reviewed.text
    assert reviewed.json()['revision']==row['revision']+1


def test_stale_candidate_cannot_apply_and_erasure_clears_all(client,cloud,queue):
    row=generated(client,cloud,queue);candidate=row['candidates'][0]
    row=upload(client,row).json();aid=row['audio_clips'][0]['id']
    row=segments(client,row,[editable(row['segments'][0],text='变更后的合成原文',audio_id=aid)]).json()
    assert review(client,row,candidate,'confirmed').status_code==409
    result=client.request('DELETE',f"/api/records/{row['id']}/audio/{aid}",json={'expected_revision':row['revision'],'confirmed':True})
    assert result.status_code==200,result.text
    assert result.json()['candidates']==[] and result.json()['qa_pairs']==[]
    with Session(client.app.state.engine) as db:
        for model in (r.RecordFieldCandidate,r.RecordFieldReview,r.RecordQAPair):
            assert db.scalar(select(func.count()).select_from(model))==0


def test_rule_match_avoids_cloud_for_exact_question(client,cloud,queue):
    enable_ai(client,'deepseek');row=with_segments(client,template_id='bride')
    field=next(f for f in row['template_snapshot']['fields'] if f['id'].startswith('q'))
    row=segments(client,row,[editable(row['segments'][0],speaker='unknown',text=f"问：{field['label']}\n答：合成原话，听明白了。")]).json()
    cloud.respond=response;start(client,row);run(queue);row=current(client,row)
    assert row['generation_job']['status']=='succeeded'
    candidate=next(x for x in row['candidates'] if x['field_id']==field['id'])
    assert candidate['origin']=='rule' and '听明白了' in candidate['value']
    import json
    for request in cloud.calls:
        content=json.loads(json.loads(request.read())['messages'][1]['content'])
        assert field['id'] not in [f['id'] for f in content['template_fields']]
    assert row['qa_pairs']


def test_earlier_answer_and_contextual_candidate_have_distinct_versions(client,cloud,queue):
    enable_ai(client,'deepseek');row=with_segments(client,template_id='bride')
    field=next(f for f in row['template_snapshot']['fields'] if f['id'].startswith('q'))
    row=segments(client,row,[editable(row['segments'][0],speaker='unknown',text=f"问：{field['label']}\n答：合成第一轮回答。\n问：你刚才说的内容是否需要补充？\n答：补充合成信息。")]).json()
    def with_evidence(request):
        import json,httpx
        data=json.loads(json.loads(request.read())['messages'][1]['content'])
        if data['template_fields'][0]['id']!=field['id']:return response(request)
        sid=row['segments'][0]['id']
        facts=[{'label':field['id'],'value':'合成第一轮回答。\n补充合成信息。','segment_ids':[sid],
                'evidence':[{'segment_id':sid,'question':field['label'],'answer':'合成第一轮回答。'},
                            {'segment_id':sid,'question':'你刚才说的内容是否需要补充？','answer':'补充合成信息。'}]}]
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'summary':'合成测试','key_facts':facts,'issues':[]})}}]})
    cloud.respond=with_evidence;start(client,row);run(queue);row=current(client,row)
    versions=[v for v in row['candidates'] if v['field_id']==field['id']]
    assert len(versions)==2
    assert any(v['origin']=='rule' and not v['latest'] and '第一轮' in v['value'] for v in versions)
    newest=next(v for v in versions if v['latest'])
    assert newest['origin']=='ai' and newest['status']=='conflict'
    assert row['qa_pairs'][1]['possible_followup_of']==0
