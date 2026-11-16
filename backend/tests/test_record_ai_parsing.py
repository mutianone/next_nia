import json
import pytest
from backend.app.records_ai import parse_analysis_response, RecordAIError

SEGMENTS = [{"segment_id":"source-1", "text":"synthetic"}]
TEMPLATE = {"fields":[{"id":"age"}]}

def parse(data, **kwargs):
    raw = data if isinstance(data,str) else json.dumps(data)
    return parse_analysis_response({"choices":[{"finish_reason":kwargs.get("finish", "stop"),"message":{"content":raw}}]}, SEGMENTS, TEMPLATE, "secret-for-test", "siliconflow", "test")

def content(value="30",source="source-1",label="age"):
    return {"summary":"合成摘要","key_facts":[{"label":label,"value":value,"segment_ids":[source]}],"issues":[]}

def test_fenced_json_and_integer_values():
    result=parse("```json\n"+json.dumps(content(30))+"\n```")
    assert result.key_facts[0].value=="30"

def test_exact_duplicate_merged():
    data=content();data["key_facts"]*=2
    assert len(parse(data).key_facts)==1

@pytest.mark.parametrize("data,code",[(content(source="fake"),"invalid_reference"),(content(label="fake"),"unknown_field"),(content(value=True),"schema"),(content(value=None),"schema"),("not json","invalid_json")])
def test_bad_output_rejected_with_precise_code(data,code):
    with pytest.raises(RecordAIError,match=code):parse(data)

def test_conflicting_values_rejected():
    data=content();data['key_facts']+=content('31')['key_facts']
    with pytest.raises(RecordAIError,match="conflicting_field"):parse(data)

def test_truncation_rejected():
    with pytest.raises(RecordAIError,match="incomplete_output"):parse(content(),finish="length")

def test_diagnostics_do_not_log_raw_values_or_unknown_keys(caplog):
    data=content();data['private-person-name']='secret-for-test'
    with pytest.raises(RecordAIError,match="sensitive_content"):parse(data)
    assert 'secret-for-test' not in caplog.text
    data=content();data['private-person-name']='private-value'
    with pytest.raises(RecordAIError,match="unknown_field"):parse(data)
    assert 'private-person-name' not in caplog.text and 'private-value' not in caplog.text

def test_no_arbitrary_json_substring_extraction():
    with pytest.raises(RecordAIError,match="invalid_json"):parse('some prose '+json.dumps(content()))


def test_unknown_metadata_without_evidence_left_blank():
    data=content('未提供');data['key_facts'][0]['segment_ids']=[]
    assert parse(data).key_facts==[]


def test_substantive_value_without_evidence_still_rejected():
    data=content('30');data['key_facts'][0]['segment_ids']=[]
    with pytest.raises(RecordAIError,match='schema'):parse(data)


def test_partition_ignores_only_other_known_template_fields():
    data=content();data['key_facts']+=content('测试姓名',label='name')['key_facts']
    template={**TEMPLATE,'grouped_generation':True,'all_field_ids':['age','name']}
    raw={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}]}
    result=parse_analysis_response(raw,SEGMENTS,template,'secret-for-test','siliconflow','test')
    assert [f.label for f in result.key_facts]==['age']
    data['key_facts'][1]['label']='invented'
    raw['choices'][0]['message']['content']=json.dumps(data)
    with pytest.raises(RecordAIError,match='unknown_field'):
        parse_analysis_response(raw,SEGMENTS,template,'secret-for-test','siliconflow','test')


def test_numeric_unit_is_not_duplicated_in_template():
    assert parse(content('30岁')).key_facts[0].value=='30'
    assert parse(content('约30岁')).key_facts[0].value=='约30岁'
