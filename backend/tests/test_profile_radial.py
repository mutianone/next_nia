from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from backend.app.database import Base
from backend.app.profile_radial import build_radial
from backend.app.people import Person

def projection(text='',facts=None,relations=None):
    return {'person':{'id':'self'},'facts':facts or [],'relations':relations or [],'sources':[{'id':'r1','type':'record','title':'测试笔录','excerpt':text}]}

def test_explicit_relations_deduplicate_and_preserve_evidence():
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        data=build_radial(s,projection('答：我父亲叫张三。\n答：我的爸爸是张三。\n答：我的丈夫叫李四。'))
        assert [(n['role'],n['name']) for n in data['relations']]==[('父亲','张三'),('丈夫','李四')]
        assert len(data['relations'][0]['sources'])==2
        assert all(n['target_id'] is None for n in data['relations'])

def test_does_not_extract_questions_negations_or_other_person_family():
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with Session(engine) as s:
        assert not build_radial(s,projection('问：我的父亲是张三。\n答：我父亲不是张三。\n答：李四的母亲是王五。'))['relations']

def test_question_answer_pair():
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with Session(engine) as s:
        rows=build_radial(s,projection('问：你的丈夫叫什么名字？\n答：李四。'))['relations']
        assert rows[0]['name']=='李四'

def test_same_names_are_candidates_not_identity_links():
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with Session(engine) as s:
        for i in range(2):s.add(Person(id=str(i),code='C'+str(i),name='李四',nationality='测试国',document_number='DOC'+str(i),source='测试'))
        s.flush()
        row=build_radial(s,projection('我的丈夫是李四。'))['relations'][0]
        assert row['target_id'] is None
        assert len(row['candidates'])==2

def test_existing_relation_links_and_archived_exclusion():
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    r={'person_id':'self','label':'父亲','source':'已核对','verified':True,'archived':False,'other':{'id':'father','name':'张三','archived':False}}
    with Session(engine) as s:
        assert build_radial(s,projection(relations=[r]))['relations'][0]['target_id']=='father'
        r['archived']=True
        assert not build_radial(s,projection(relations=[r]))['relations']

def test_confirmed_relation_and_repeated_transcript_merge_sources():
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    relation={'person_id':'self','label':'父亲','source':'已核对材料','verified':True,'archived':False,'other':{'id':'father','name':'测试父亲','archived':False}}
    with Session(engine) as s:
        rows=build_radial(s,projection('答：我的父亲叫测试父亲。\n答：我的爸爸是测试父亲。',relations=[relation]))['relations']
        assert len(rows)==1
        assert rows[0]['target_id']=='father'
        assert rows[0]['verified'] is True
        assert len(rows[0]['sources'])==3

def test_personal_database_priority_and_transcript_fallback():
    from backend.app.profile_radial import resolve_personal
    profile=projection('我的国籍是老挝。我现在住在测试市和平路八号。我来中国的目的是结婚。')
    profile['person'].update(nationality='档案国籍',address='',purpose=None)
    result=resolve_personal(profile)
    assert result['nationality']['value']=='档案国籍'
    assert result['nationality']['source']=='person'
    assert result['address']['value']=='测试市和平路八号'
    assert result['purpose']['value']=='结婚'
    assert result['address']['sources'][0]['record_id']=='r1'
    assert profile['person']['address']==''

def test_personal_conflicts_questions_and_uncertainty():
    from backend.app.profile_radial import resolve_personal
    result=resolve_personal(projection('问：我现在住在甲市。\n答：我现在住在乙市。\n答：我现在住在丙市。我的国籍是不确定。十月可能十号入境。'))
    assert result['address']['conflict'] and result['address']['value']==''
    assert 'nationality' not in result
    assert 'entry_date' not in result


def test_asr_missing_punctuation_does_not_append_officer_question():
    from backend.app.profile_radial import resolve_personal
    result=resolve_personal(projection('我现在住在测试室和平路8号8号是你现在住的地方，还是以前住的地方？'))
    assert result['address']['value']=='测试室和平路8号'


def test_same_address_in_transcript_and_template_is_not_a_conflict():
    from backend.app.profile_radial import resolve_personal
    profile = projection('我现在住在测试市和平路8号嗯，和我丈夫一起住。')
    profile['sources'][0]['template_values'] = {'address': '我现在住在测试市和平路8号嗯，和我丈夫一起住。'}
    result = resolve_personal(profile)['address']
    assert result['value'] == '测试市和平路8号'
    assert not result['conflict']
    assert len(result['sources']) == 2
    profile['sources'][0]['template_values']['address'] = '测试市和平路9号'
    result = resolve_personal(profile)['address']
    assert result['conflict'] and result['value'] == ''
