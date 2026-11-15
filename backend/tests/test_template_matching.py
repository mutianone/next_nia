from copy import deepcopy
import pytest
from backend.app.record_templates import CASE_TEMPLATES
from backend.app.record_template_questions import parse_template
from backend.app.record_template_matcher import match_pairs

def template(fields=None):return parse_template({'id':'test','version':'1','fields':fields or [{'id':'q_name','label':'你叫什么名字？','question_aliases':['请问你的姓名']} ]})
def pair(q='你叫什么名字？',a='金子。',key='qa1'):return {'qa_pair_id':key,'question':q,'answer':a}

@pytest.mark.parametrize('t',CASE_TEMPLATES,ids=lambda t:t['id'])
def test_original_templates_parse_with_same_ids(t):
    original=deepcopy(t);parsed=parse_template(t)
    assert [f['id'] for f in parsed['fields']]==[f['id'] for f in t['fields']]
    assert t==original and not next(f for f in parsed['fields'] if f['id']=='start_year')['fillable']

def test_exact_alias_unmatched_and_priority():
    t=template();ps=[pair(),pair('请问你的姓名',key='qa2'),pair('讲讲其他情况',key='qa3')]
    result=match_pairs(ps,t)
    assert [x['match_method'] for x in result]==['RULE','ALIAS','UNMATCHED']
    assert match_pairs([pair()],t,bindings={'qa1':[]})[0]['template_field_id'] is None

def test_shared_exclusive_and_free_questions():
    fields=[dict(id='one',label='同一个问题',allow_shared_source=True),dict(id='two',label='同一个问题',allow_shared_source=True)]
    r=match_pairs([pair('同一个问题')],template(fields));assert len(r)==2 and all(not x['problems'] for x in r)
    for f in fields:f['exclusive_group']='identity'
    assert all('EXCLUSIVE_FIELDS' in x['problems'] for x in match_pairs([pair('同一个问题')],template(fields)))
    free=template([dict(id='free',label='自由问答',kind='free_qa',allow_multiple=True)])
    assert match_pairs([pair('其他问题')],free)[0]['match_method']=='FREE_QA'

@pytest.mark.parametrize('bad',[{'id':'x','version':'1','fields':[{'id':'same','label':'A'},{'id':'same','label':'B'}]}, {'id':'x','version':'1','fields':[{'id':'x','label':'','question_aliases':[]}]}, {'id':'x','version':'1','fields':[{'id':'x','label':'A','question_aliases':'not-list'}]}])
def test_bad_definitions_fail(bad):
    with pytest.raises(ValueError):parse_template(bad)

def test_semantic_ids_only_and_no_answer_rewrite():
    p=pair('请告诉我怎么称呼您',a='我叫金子。\n也有人叫我阿金。')
    result=match_pairs([p],template(),semantic={'qa1':['q_name']})[0]
    assert result['match_method']=='SEMANTIC' and result['qa']['answer']==p['answer']
    with pytest.raises(ValueError):match_pairs([p],template(),semantic={'qa1':['invented']})

def test_explicit_unmatched_does_not_fall_back_to_free_field():
    t=template([{'id':'free','label':'自由问答','kind':'free_qa'}])
    assert match_pairs([pair()],t,bindings={'qa1':[]})[0]['template_field_id'] is None


def test_fixed_acceptance_metrics():
    # Ten labelled synthetic cases, including two deliberate abstentions.
    cases=[]
    for t in CASE_TEMPLATES:
        for f in [f for f in t['fields'] if f['id'].startswith('q')][:2]:
            cases.append((parse_template(t),pair(f['label'],a='听明白了。'),f['id']))
    t=template([{'id':'q_name','label':'你叫什么名字？','question_aliases':['请问你的姓名','您怎么称呼']}])
    cases.extend([(t,pair('请问你的姓名'),'q_name'),(t,pair('您怎么称呼'),'q_name'),(t,pair('你不叫金子，对吗？'),None),(t,pair('谈一下其他事情'),None)])
    outputs=[match_pairs([p],t)[0] for t,p,_ in cases]
    matched=[(o,expected) for o,(_,_,expected) in zip(outputs,cases) if o['template_field_id'] is not None]
    assert len(cases)==10 and len(matched)==8
    assert sum(o['template_field_id']==expected for o,expected in matched)==8
    assert sum(o['template_field_id'] is None for o in outputs)==2
    assert all(o['qa']['answer']==p['answer'] for o,(_,p,_) in zip(outputs,cases))
