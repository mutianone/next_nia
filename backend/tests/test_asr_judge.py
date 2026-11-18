from copy import deepcopy
import json
import pytest
from backend.app.record_asr_judge import recommend, critical_difference
from backend.app.record_dual_asr import combine
from backend.app import records_ai
from backend.tests.test_dual_asr import settings, output, pair, transcribed
from backend.tests.test_record_capture import client


def response(choices):
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'choices':choices},ensure_ascii=False)}}]}


def different():
    return combine({'siliconflow':output(['请介绍你的兴趣。','我平时喜欢打蓝球，周末也去跑步。'],'siliconflow'),
                    'volcengine':output(['请介绍你的兴趣。','我平时喜欢打篮球，周末也去跑步。'],'volcengine')})


def test_identical_text_prefers_independent_short_answer_without_model(monkeypatch):
    q = output(['你看过吗？','我看过啊。'],'siliconflow')
    v = output(['你看过吗？我看过啊。'],'volcengine');v['segments'][0]['end_ms']=3100
    data=combine({'siliconflow':q,'volcengine':v});original=deepcopy(data['raw_result'])
    monkeypatch.setattr(records_ai,'bounded_post',lambda *a,**kw:pytest.fail('no model needed'))
    recommend(data,settings())
    chosen=data['segments'][0]['dual_review']['recommendation']
    assert chosen['provider']=='siliconflow' and chosen['method']=='rules' and chosen['review_required']
    assert data['raw_result']==original


@pytest.mark.parametrize('a,b', [('我确定','我不确定'),('证件号码123456','证件号码123457'),
    ('我同意','我反对'),('你叫什么名字张三','你叫什么名字章三')])
def test_critical_disputes_abstain(a,b):
    assert critical_difference(a,b)


def test_model_selects_existing_candidate_and_never_edits_sources(monkeypatch):
    s=settings();s.ai.speech.dual_judge='default';data=different();original=deepcopy(data['segments'])
    def call(url,key,**kw):
        prompt=json.loads(kw['json']['messages'][1]['content'])
        assert 'provider' not in str(prompt)
        group=prompt['groups'][0]
        better=next(c['id'] for c in group['candidates'] if '篮球' in c['text'])
        return response([{'group':group['group'],'choice':better,'reason':'另一候选有疑似同音错字，但需原音确认。'}])
    monkeypatch.setattr(records_ai,'bounded_post',call)
    recommend(data,s)
    assert data['segments'][1]['dual_review']['recommendation']['provider']=='volcengine'
    assert data['segments'][1]['dual_review']['recommendation']['method']=='model'
    assert [g['text'] for g in data['segments']]==[g['text'] for g in original]
    assert data['segments'][1]['dual_review']['candidates']==original[1]['dual_review']['candidates']


@pytest.mark.parametrize('mode,permission',[('rules',True),('deepseek',False)])
def test_no_text_transmission_without_mode_and_permission(monkeypatch,mode,permission):
    s=settings();s.ai.speech.dual_judge=mode;s.ai.external_permissions.analysis=permission
    monkeypatch.setattr(records_ai,'bounded_post',lambda *a,**kw:pytest.fail('must not send'))
    data=recommend(different(),s)
    assert data['segments'][1]['dual_review']['recommendation']['provider'] is None


@pytest.mark.parametrize('failure',['invalid','truncated','exception','invented','wrong_type'])
def test_bad_model_result_preserves_asr_and_abstains(monkeypatch,failure):
    s=settings();s.ai.speech.dual_judge='default';data=different()
    def call(*a,**kw):
        if failure=='wrong_type':return {'choices':[False]}
        if failure=='exception':raise records_ai.RecordAIError(504,'test secret')
        if failure=='invalid':return response([])
        if failure=='invented':return response([{'group':1,'choice':'C','reason':'自造文字'}])
        result=response([{'group':1,'choice':'A','reason':'截断'}]);result['choices'][0]['finish_reason']='length';return result
    monkeypatch.setattr(records_ai,'bounded_post',call)
    recommend(data,s)
    assert data['segments'][0]['dual_review']['recommendation']['provider']
    assert data['segments'][1]['dual_review']['recommendation']['provider'] is None
    assert 'test secret' not in str(data)


def test_dropping_supplement_is_vetoed(monkeypatch):
    s=settings();s.ai.speech.dual_judge='default'
    data=combine({'siliconflow':output(['我喜欢篮球也喜欢跑步。'],'siliconflow'),
                  'volcengine':output(['我喜欢篮球也喜欢跑步。还有其他爱好。'],'volcengine')})
    def call(*a,**kw):
        group=json.loads(kw['json']['messages'][1]['content'])['groups'][0]
        shorter=min(group['candidates'],key=lambda c:len(c['text']))
        return response([{'group':0,'choice':shorter['id'],'reason':'更简洁'}])
    monkeypatch.setattr(records_ai,'bounded_post',call)
    assert recommend(data,s)['segments'][0]['dual_review']['recommendation']['provider'] is None


def test_old_version_recommendations_do_not_save_or_unlock_fill(client,monkeypatch):
    row=transcribed(client,monkeypatch);root=f"/api/records/{row['id']}"
    material=client.get(root+'/dual-review').json();run=material['runs'][0]
    response=client.post(root+'/dual-review/'+run['id']+'/recommend',json={'expected_revision':row['revision'],'confirmed':True})
    assert response.status_code==200,response.text
    assert response.json()['groups'][0]['recommendation']['provider']
    assert response.json()['groups'][1]['recommendation']['provider'] is None
    assert client.get(root+'/dual-review').json()==material
    assert client.post(root+'/analyze',json={'expected_revision':row['revision'],'confirmed':True}).status_code==409


def test_deepseek_can_be_chosen_without_changing_default_provider(monkeypatch):
    from pydantic import SecretStr
    s=settings();s.ai.speech.dual_judge='deepseek';s.ai.providers.deepseek.enabled=True
    s.ai.providers.deepseek.api_key=SecretStr('fake-deepseek-secret')
    def call(url,key,**kw):
        assert url==records_ai.CHAT_URLS['deepseek'] and key=='fake-deepseek-secret'
        assert kw['json']['model']==s.ai.providers.deepseek.chat_model
        assert kw['json']['thinking']=={'type':'disabled'}
        return response([{'group':1,'choice':'ABSTAIN','reason':'不能听音确认'}])
    monkeypatch.setattr(records_ai,'bounded_post',call)
    data=recommend(different(),s)
    assert data['segments'][1]['dual_review']['recommendation']['judge_provider']=='deepseek'
    assert s.ai.default_provider=='siliconflow' and 'fake-deepseek-secret' not in str(data)
