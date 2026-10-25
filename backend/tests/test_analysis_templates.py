import json
from backend.tests.test_knowledge import client, enable_ai
from backend.app.analysis_templates import seed_analysis_templates

PATH='/api/knowledge/analysis/templates'

def test_defaults_edit_seed_and_add(client):
    items=client.get(PATH).json()['items']
    assert len(items)==2
    assert {x['name'] for x in items}=={'涉嫌非法居留','涉嫌非法就业'}
    body={k:v for k,v in items[0].items() if k!='id'}
    body['name']='修订后的模板'
    saved=client.put(PATH+'/'+items[0]['id'],json=body)
    assert saved.status_code==200 and saved.json()['revision']==2
    assert client.put(PATH+'/'+items[0]['id'],json=body).status_code==409
    seed_analysis_templates(client.app.state.engine)
    assert client.get(PATH).json()['items'][0]['name']=='修订后的模板'
    assert client.post(PATH,json=body).status_code==200
    body['bases'][0]['source']='javascript:alert(1)'
    assert client.post(PATH,json=body).status_code==422

def test_template_stream_uses_local_basis_and_fact_checklist(client,monkeypatch):
    enable_ai(client)
    seen=[]
    def fake(question,sources,settings,**kwargs):
        assert kwargs == {"analysis": True}
        seen.append((json.loads(question),sources))
        yield {'type':'complete','answer':{'status':'answered','answer':'辅助分析','claims':[],'sources':sources,'provider':'siliconflow','model':'test'}}
    monkeypatch.setattr('backend.app.knowledge.stream_answer',fake)
    body={'question':'已超过允许停留期限，延期手续尚待核实。','template_id':'builtin-illegal-stay','template_revision':1,'as_of':'2026-10-25','confirm_external':True}
    response=client.post('/api/knowledge/ask/stream',json=body)
    assert response.status_code==200 and '辅助分析' in response.text
    question,sources=seen[0]
    assert '核查清单' in question and len(sources)==4
    assert all(x['template_source'] for x in sources)
    assert '第十一日' in sources[2]['text']
    body['as_of']='2012-01-01'
    assert client.post('/api/knowledge/ask/stream',json=body).status_code==409
    body['as_of']='2026-10-25';body['template_revision']=99
    assert client.post('/api/knowledge/ask/stream',json=body).status_code==409
    assert len(seen)==1

def test_template_write_permission():
    from backend.app.auth import permitted
    for role in ('business','viewer'):
        assert not permitted(role,'POST',PATH,{})
        assert not permitted(role,'PUT',PATH+'/builtin-illegal-stay',{})
        assert permitted(role,'GET',PATH,{})
