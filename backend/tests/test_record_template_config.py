from backend.tests.test_phase5 import client
from backend.tests.test_records import cloud, enable_ai, create, segments, analyze, analysis_response


def payload(template):
    return {'expected_revision':template.get('config_revision',0),'name':template['name'],'description':template.get('description',''),'scenario':template.get('scenario','other'),'enabled':True,'fields':[{k:f.get(k,default) for k,default in [('id',''),('label',''),('description',''),('question_aliases',[]),('required',False)]} for f in template['fields']]}


def test_template_versions_snapshot_and_permissions(client):
    template=client.get('/api/record-templates').json()['items'][0]
    prior=create(client,template_id=template['id'])
    body=payload(template);body['fields'][0].update(description='合成配置说明',required=True)
    saved=client.put('/api/record-templates/'+template['id'],json=body)
    assert saved.status_code==200,saved.text
    assert saved.json()['config_revision']==1
    assert client.put('/api/record-templates/'+template['id'],json=body).status_code==409
    assert client.get('/api/records/'+prior['id']).json()['template_snapshot'].get('config_revision')==0
    newer=create(client,template_id=template['id'])
    assert newer['template_snapshot']['fields'][0]['description']=='合成配置说明'
    assert newer['template_snapshot']['version']!=prior['template_snapshot']['version']
    body['expected_revision']=1;body['fields'][0]['label']='不允许替换原文固定问题'
    assert client.put('/api/record-templates/'+template['id'],json=body).status_code==422
    assert len(client.get('/api/record-templates/'+template['id']+'/versions').json()['items'])==1
    account=client.post('/api/accounts',json={'phone':'13800138000','password':'abc12345','name':'合成业务账号','role':'business'})
    assert account.status_code==201,account.text
    assert client.post('/api/auth/login',json={'phone':'13800138000','password':'abc12345'}).status_code==200
    assert client.get('/api/record-templates').status_code==200
    assert client.put('/api/record-templates/'+template['id'],json=payload(saved.json())).status_code==403


def test_custom_template_required_fields_and_disable(client,cloud):
    body={'expected_revision':0,'name':'合成自定义模板','fields':[{'id':'q1','label':'你的现住址在哪里？','question_aliases':['你住哪里？'],'required':True}]}
    saved=client.put('/api/record-templates/custom_test',json=body)
    assert saved.status_code==200,saved.text
    row=create(client,template_id='custom_test')
    row=segments(client,row,[{'text':'合成问答原文','speaker':'interviewee'}]).json()
    enable_ai(client);cloud.respond=lambda _:analysis_response(row['segments'][0]['id'],key_facts=[])
    result=analyze(client,row);assert result.status_code==200,result.text;row=result.json()
    check={'expected_revision':row['revision'],'status':'reviewed','confirmed':True,'review_note':'合成核对'}
    assert client.patch('/api/records/'+row['id'],json=check).status_code==422
    row=client.patch('/api/records/'+row['id']+'/template-values',json={'expected_revision':row['revision'],'values':{'q1':'合成测试地址'}}).json()
    assert client.patch('/api/records/'+row['id'],json={**check,'expected_revision':row['revision']}).status_code==200
    assert client.get('/api/records/'+row['id']+'/export.docx').status_code==200
    disabled=client.put('/api/record-templates/custom_test',json={**body,'expected_revision':1,'enabled':False});assert disabled.status_code==200
    assert 'custom_test' not in [t['id'] for t in client.get('/api/records/capabilities').json()['templates']]
    assert client.post('/api/records',json={'title':'不能使用停用模板','template_id':'custom_test'}).status_code==422
    assert client.get('/api/records/'+row['id']).json()['template_snapshot']['config_revision']==1


def test_switching_templates_keeps_qa_mappings_separate(client,cloud):
    import json
    for name,key in [('custom_a','q1'),('custom_b','q2')]:
        response=client.put('/api/record-templates/'+name,json={'expected_revision':0,'name':name,'fields':[{'id':key,'label':'你住哪里？'}]})
        assert response.status_code==200,response.text
    row=create(client,template_id='custom_a')
    row=segments(client,row,[{'text':'问：你住哪里？\n答：合成测试地址。','speaker':'unknown'}]).json()
    enable_ai(client)
    def answer(request):
        data=json.loads(json.loads(request.read())['messages'][1]['content'])
        return analysis_response(row['segments'][0]['id'],key_facts=[{'label':data['template_fields'][0]['id'],'value':'合成测试地址。','segment_ids':[row['segments'][0]['id']]}])
    cloud.respond=answer
    row=analyze(client,row).json();assert row['qa_pairs'][0]['field_id']=='q1'
    row=client.patch('/api/records/'+row['id'],json={'expected_revision':row['revision'],'template_id':'custom_b'}).json()
    assert row['qa_pairs']==[]
    row=analyze(client,row).json();assert row['qa_pairs'][0]['field_id']=='q2'
