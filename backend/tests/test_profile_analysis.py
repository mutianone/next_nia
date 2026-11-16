import pytest

@pytest.fixture(autouse=True)
def immediate_cloud(monkeypatch):
    monkeypatch.setattr('backend.app.record_cloud.reserve',lambda:None)
    monkeypatch.setattr('backend.app.record_cloud.time.sleep',lambda _:None)

import json
import pytest
from backend.app.profile_analysis import parse_output,materials
from backend.app.main import create_app
from backend.tests.auth_client import TestClient
from backend.app import profile_analysis as module
from backend.app.records_ai import RecordAIError
from sqlalchemy.orm import Session

SOURCES=[{'id':'s1','text':'我现在住在和平路八号。','title':'测试'}]
def response(quote='我现在住在和平路八号。'):
    return {'choices':[{'message':{'content':json.dumps({'findings':[{'category':'summary','title':'居住陈述','conclusion':'本人陈述住在和平路八号，待核对。','alternatives':[],'questions':['何时搬入？'],'citations':[{'source_id':'s1','quote':quote}]}]},ensure_ascii=False)}}]}

def test_citation_must_exist_and_match():
    assert len(parse_output(response(),SOURCES)['findings'])==1
    with pytest.raises(ValueError):parse_output(response('我住在不存在的地方'),SOURCES)
    with pytest.raises(ValueError):parse_output(response(),[])

def test_empty_analysis_is_valid():
    assert parse_output({'choices':[{'message':{'content':'{"findings":[]}'}}]},SOURCES)['findings']==[]

def test_api_persists_and_invalidates_on_change(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'provider_config',lambda *_:('siliconflow','test-model','test-key'))
    def fake(*a,**kw):
        sources=json.loads(kw['json']['messages'][1]['content'])
        result=response(sources[0]['text'].splitlines()[0])
        body=json.loads(result['choices'][0]['message']['content']);body['findings'][0]['citations'][0]['source_id']=sources[0]['id']
        result['choices'][0]['message']['content']=json.dumps(body)
        return result
    monkeypatch.setattr(module,'bounded_post',fake)
    app=create_app(tmp_path)
    with TestClient(app) as c:
        p=c.post('/api/people',json={'name':'虚构测试','nationality':'测试国','document_number':'TEST-NEW-01','source':'测试'}).json()
        url=f"/api/profiles/{p['id']}/analysis"
        assert c.get(url).json()['job'] is None
        assert c.post(url,json={'record_ids':[]}).status_code==202
        job=c.get(url).json()['job'];assert job['status']=='succeeded';assert job['result']['findings']
        from backend.app.people import Person
        with Session(app.state.engine) as session:
            session.get(Person,p['id']).address='新的地址';session.commit()
        job=c.get(url).json()['job'];assert job['stale'];assert job['result'] is None
        assert c.post(url,json={'record_ids':['another-person-record']}).status_code==409
        monkeypatch.setattr(module,'bounded_post',lambda *a,**k:(_ for _ in ()).throw(RecordAIError(504,'测试超时')))
        assert c.post(url,json={'record_ids':[]}).status_code==202
        assert c.get(url).json()['job']['status']=='failed'


def test_missing_archive_field_is_not_a_conflict():
    raw=response()
    body=json.loads(raw['choices'][0]['message']['content'])
    body['findings'][0]['title']='居住地址与档案不一致'
    raw['choices'][0]['message']['content']=json.dumps(body)
    with pytest.raises(ValueError):parse_output(raw,SOURCES+[{'id':'person:x','text':'address: 未登记（不是矛盾证据）'}])


def test_archive_quotation_restores_omitted_lines_only():
    raw=response('name: 测试\nnationality: 测试国')
    data=json.loads(raw['choices'][0]['message']['content']);data['findings'][0]['citations'][0]['source_id']='person:x'
    raw['choices'][0]['message']['content']=json.dumps(data)
    text='name: 测试\nenglish_name: 未登记\nnationality: 测试国'
    assert parse_output(raw,[{'id':'person:x','text':text}])['findings'][0]['citations'][0]['quote']==text

def test_only_current_generated_unarchived_asr_records_are_materials(tmp_path):
    from backend.app.records import RecordSession,RecordSegment,segment_hash
    from fastapi import HTTPException
    app=create_app(tmp_path)
    with TestClient(app) as c:
        p=c.post('/api/people',json={'name':'材料测试','nationality':'测试国','document_number':'ELIGIBLE-01','source':'测试'}).json()
        record=c.post('/api/records',json={'title':'测试笔录','scenario':'marriage','template_id':'bride','person_id':p['id']}).json()
        url=f"/api/profiles/{p['id']}/analysis"
        assert c.get(url).json()['records']==[]
        with Session(app.state.engine) as session:
            row=session.get(RecordSession,record['id'])
            segment=RecordSegment(record_id=row.id,position=0,source='asr',text='我叫测试人员。')
            session.add(segment);session.flush()
            with pytest.raises(HTTPException):materials(session,p['id'],[row.id])
            row.template_snapshot={**row.template_snapshot,'generated':True,'values':{'name':'测试人员'},'source_hash':segment_hash([segment])}
            session.commit()
        assert len(c.get(url).json()['records'])==1
        with Session(app.state.engine) as session:
            row=session.get(RecordSession,record['id']);sources,_=materials(session,p['id'],[row.id]);assert any(x['id']=='document:'+row.id for x in sources)
            row.status='archived';session.commit()
            with pytest.raises(HTTPException):materials(session,p['id'],[row.id])
        assert c.get(url).json()['records']==[]
        with Session(app.state.engine) as session:
            row=session.get(RecordSession,record['id']);row.status='draft'
            segment=session.scalar(module.select(RecordSegment).where(RecordSegment.record_id==row.id));segment.text='转写更正';session.commit()
            with pytest.raises(HTTPException):materials(session,p['id'],[row.id])
        assert c.get(url).json()['records']==[]

def test_cross_scope_validation_isolation_and_archive_invalidation(tmp_path,monkeypatch):
    from backend.app.people import Person
    monkeypatch.setattr(module,'provider_config',lambda *_:('siliconflow','test','key'))
    def cloud(*args,**kwargs):
        sources=json.loads(kwargs['json']['messages'][1]['content'])
        people=[s for s in sources if s['id'].startswith('person:')]
        data={'findings':[{'category':'connection','title':'共同联系人待核实','conclusion':'两人均提到李明，尚不能认定同一人。','alternatives':['可能同名'],'questions':['核对联系方式'], 'citations':[{'source_id':s['id'],'quote':s['text'].splitlines()[0]} for s in people]}]}
        return {'choices':[{'message':{'content':json.dumps(data,ensure_ascii=False)}}]}
    monkeypatch.setattr(module,'bounded_post',cloud)
    app=create_app(tmp_path)
    with TestClient(app) as c:
        people=[c.post('/api/people',json={'name':f'虚构对比{i}','nationality':'测试国','document_number':f'CROSS-{i}','source':'测试','notes':'介绍人李明'}).json() for i in range(3)]
        pid,other=people[0]['id'],people[1]['id'];url=f'/api/profiles/{pid}/analysis'
        assert c.post(url,json={'mode':'cross','record_ids':[]}).status_code==422
        assert c.post(url,json={'mode':'overview','person_ids':[other],'record_ids':[]}).status_code==422
        assert c.post(url,json={'mode':'cross','person_ids':[other],'record_ids':[]}).status_code==202
        result=c.get(url+'?mode=cross').json()['job'];assert result['status']=='succeeded'
        assert {s['person_id'] for s in result['result']['sources']}=={pid,other}
        assert c.get(url).json()['job'] is None
        with Session(app.state.engine) as session:session.get(Person,other).archived=True;session.commit()
        assert c.get(url+'?mode=cross').json()['job']['stale']
        assert c.get(url+'?mode=cross').json()['job']['result'] is None
        assert c.post(url,json={'mode':'cross','person_ids':[other],'record_ids':[]}).status_code==409

def test_consistency_rejects_single_evidence(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'provider_config',lambda *_:('siliconflow','test','key'))
    def cloud(*a,**kw):
        s=json.loads(kw['json']['messages'][1]['content'])[0]
        data=json.loads(response(s['text'].splitlines()[0])['choices'][0]['message']['content'])
        data['findings'][0]['category']='check';data['findings'][0]['citations'][0]['source_id']=s['id']
        return {'choices':[{'message':{'content':json.dumps(data)}}]}
    monkeypatch.setattr(module,'bounded_post',cloud)
    app=create_app(tmp_path)
    with TestClient(app) as c:
        p=c.post('/api/people',json={'name':'虚构一致性测试','nationality':'测试国','document_number':'CONSIST-1','source':'测试'}).json()
        url=f"/api/profiles/{p['id']}/analysis"
        assert c.post(url,json={'mode':'consistency','record_ids':[]}).status_code==202
        assert c.get(url+'?mode=consistency').json()['job']['status']=='failed'
