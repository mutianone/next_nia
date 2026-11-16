import json
import pytest
from backend.tests.test_records import client, cloud, create, with_segments, enable_ai, analyze, analysis_response, segments, editable

def generated(client,cloud):
    enable_ai(client,'deepseek');row=with_segments(client)
    cloud.respond=lambda request: analysis_response(row['segments'][0]['id'],key_facts=[{'label':'facts','value':'合成询问事实','segment_ids':[row['segments'][0]['id']]}])
    response=analyze(client,row);assert response.status_code==200,response.text
    return response.json()

def test_generation_correction_export_and_review(client,cloud):
    row=generated(client,cloud);template=row['template_snapshot']
    assert template['values']=={'facts':'合成询问事实'}
    assert '姓名：________' in row['rendered_text']
    sent=json.loads(json.loads(cloud.calls[0].read())['messages'][1]['content'])
    assert sent['template_fields']==[{'id':field['id'],'question_or_label':field['label']} for field in template['fields']]
    response=client.patch(f"/api/records/{row['id']}/template-values",json={'expected_revision':row['revision'],'values':{'name':'测试姓名'}})
    assert response.status_code==200,response.text
    row=response.json();assert '姓名：测试姓名' in row['rendered_text']
    exported=client.get(f"/api/records/{row['id']}/export.txt")
    assert exported.status_code==200 and '姓名：测试姓名' in exported.text
    assert row['segments'][0]['text']=='我在测试日期到达，仅为合成陈述。'
    response=client.patch(f"/api/records/{row['id']}",json={'expected_revision':row['revision'],'status':'reviewed','confirmed':True,'review_note':'已核对合成资料'})
    assert response.status_code==200,response.text
    assert '姓名：测试姓名' in response.json()['rendered_text']
    assert client.patch(f"/api/records/{row['id']}/template-values",json={'expected_revision':response.json()['revision'],'values':{'name':'修改'}}).status_code==409

def test_changed_transcript_requires_regeneration(client,cloud):
    row=generated(client,cloud)
    row=segments(client,row,[editable(row['segments'][0],text='新的合成陈述')]).json()
    assert '已变化' in row['rendered_text']
    assert client.patch(f"/api/records/{row['id']}",json={'expected_revision':row['revision'],'status':'reviewed','confirmed':True,'review_note':'已核对'}).status_code==422
    assert client.patch(f"/api/records/{row['id']}/template-values",json={'expected_revision':row['revision'],'values':{'name':'修改'}}).status_code==409

@pytest.mark.parametrize('label,source',[('unknown',None),('name','invented')])
def test_invalid_field_or_source_not_applied(client,cloud,label,source):
    enable_ai(client,'deepseek');row=with_segments(client)
    cloud.respond=lambda request:analysis_response(row['segments'][0]['id'],key_facts=[{'label':label,'value':'测试','segment_ids':[source or row['segments'][0]['id']]}])
    assert analyze(client,row).status_code==502
    actual=client.get(f"/api/records/{row['id']}").json()
    assert not actual['template_snapshot'].get('generated')
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from backend.app.records import RecordAnalysis
    with Session(client.app.state.engine) as session:
        attempt=session.scalar(select(RecordAnalysis).where(RecordAnalysis.record_id==row['id']))
        assert ('invalid_reference' if source else 'unknown_field') in attempt.error


def test_provided_template_choices_and_independent_binding(client):
    choices=client.get('/api/records/capabilities').json()['templates']
    assert [item['id'] for item in choices]==['bride','bride_family','border_case']
    assert [item['name'] for item in choices]==['询问笔录模板(外籍新娘)','询问笔录模板(外籍新娘的家属)','讯问笔录模板（组织他人偷越国（边）境、提供变造护照）']
    for template in choices:
        row=create(client,template_id=template['id'],scenario=template['scenario'])
        assert row['template_snapshot']['name']==template['name']
        updated=client.patch(f"/api/records/{row['id']}",json={'expected_revision':row['revision'],'scenario':'other'})
        assert updated.status_code==200,updated.text
        assert updated.json()['template_id']==template['id']
        assert updated.json()['template_snapshot']['name']==template['name']


@pytest.mark.parametrize('template_id',['bride','bride_family','border_case'])
def test_original_word_template_filled_and_samples_removed(client,cloud,template_id):
    from io import BytesIO
    from zipfile import ZipFile
    from xml.etree import ElementTree as ET
    from backend.app.record_word import ROOT,W
    enable_ai(client,'deepseek');row=with_segments(client,template_id=template_id)
    template=row['template_snapshot'];question=next(f for f in template['fields'] if f['id'].startswith('q') and '家庭情况' in f['label'])
    # Explicit human-reviewed turns: a question is not interviewee speech.
    answer='家庭情况合成回答，仅用于测试。'
    row=segments(client,row,[{'speaker':'interviewer','text':question['label']},
                             {'speaker':'interviewee','text':answer},
                             {'speaker':'interviewee','text':'我的姓名是测试人员。'}]).json()
    q,a,name=row['segments']
    cloud.respond=lambda request:analysis_response(a['id'],key_facts=[
        {'label':'name','value':'测试人员','segment_ids':[name['id']]},
        {'label':question['id'],'value':answer,'segment_ids':[q['id'],a['id']],
         'evidence':[{'segment_id':a['id'],'question_segment_id':q['id'],'question':question['label'],'answer':answer}]}])
    response=analyze(client,row);assert response.status_code==200,response.text
    current=response.json();assert '家庭情况合成回答' in current['rendered_text']
    assert '答：属实' not in current['rendered_text'] and '答：明白' not in current['rendered_text']
    assert '先引导其陈述动机' not in current['rendered_text']
    result=client.get(f"/api/records/{row['id']}/export.docx")
    assert result.status_code==200,result.text[:100] if result.status_code!=200 else ''
    with ZipFile(BytesIO(result.content)) as z,ZipFile(ROOT/template['word_asset']) as source:
        xml=ET.fromstring(z.read('word/document.xml'));old=ET.fromstring(source.read('word/document.xml'))
        text=''.join(xml.itertext());assert '测试人员' in text and '家庭情况合成回答' in text
        assert '答：属实' not in text and '上家防止我接到妇女后跑掉' not in text
        assert ET.tostring(xml.find('.//'+W+'sectPr'))==ET.tostring(old.find('.//'+W+'sectPr'))
        assert [ET.tostring(x) for x in xml.iter(W+'pPr')]==[ET.tostring(x) for x in old.iter(W+'pPr')]
        for name in source.namelist():
            if name!='word/document.xml':assert z.read(name)==source.read(name)


def test_question_bindings_have_unique_fields_and_answer_slots():
    from backend.app.record_templates import CASE_TEMPLATES
    from backend.app.record_word import render_original
    for template in CASE_TEMPLATES:
        assert len({f['id'] for f in template['fields']})==len(template['fields'])
        assert len(template['fields'])>30
        text=render_original(template)
        assert text.count('答：')==sum(f['id'].startswith('q') for f in template['fields'])
        assert '答：属实' not in text


def test_archived_records_only_appear_in_archived_filter(client):
    active=create(client,title='同名测试：未归档')
    archived=create(client,title='同名测试：归档')
    response=client.patch(f"/api/records/{archived['id']}",json={'expected_revision':archived['revision'],'status':'archived'})
    assert response.status_code==200
    for query in ('','?status=all','?status=all&q=同名测试&page_size=1'):
        result=client.get('/api/records'+query).json()
        assert result['total']==1
        assert [item['id'] for item in result['items']]==[active['id']]
        assert result['stats']['total']==1 and result['stats']['archived']==1
    result=client.get('/api/records?status=archived&q=同名测试').json()
    assert result['total']==1 and result['items'][0]['id']==archived['id']
    restored=client.patch(f"/api/records/{archived['id']}",json={'expected_revision':response.json()['revision'],'status':'draft'})
    assert restored.status_code==200
    assert client.get('/api/records').json()['total']==2
    assert client.get('/api/records?status=archived').json()['total']==0


@pytest.mark.parametrize('text,value,keep', [
    ('国籍是柬埔寨，父母住在柬埔寨。', '柬埔寨', False),
    ('询问人：户籍在哪里？被询问人：不清楚。国籍是柬埔寨。', '柬埔寨', False),
    ('被询问人：我的户籍所在地是测试市甲镇。', '测试市甲镇', True),
])
def test_domicile_requires_explicit_evidence(text,value,keep):
    from backend.app.records_ai import AnalysisOutput, grounded_template_facts
    output=AnalysisOutput(summary='测试',key_facts=[{'label':'registered_address','value':value,'segment_ids':['s1']}],issues=[])
    grounded_template_facts(output,[{'segment_id':'s1','text':text}])
    assert bool(output.key_facts)==keep

@pytest.mark.parametrize('value,keep',[('电话号码我记不清楚',False),('未提供',False),('电话号码',False),('010-00000000',True)])
def test_unknown_contact_remains_blank(value,keep):
    from backend.app.records_ai import AnalysisOutput, grounded_template_facts
    output=AnalysisOutput(summary='测试',key_facts=[{'label':'contact','value':value,'segment_ids':['s1']}],issues=[])
    grounded_template_facts(output,[{'segment_id':'s1','text':value}])
    assert bool(output.key_facts)==keep
