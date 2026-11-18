"""Versioned field schemas and original Word question/answer bindings."""
from .record_word import definition, render_original
COMMON = [('name','被询问人姓名'),('nationality','国籍'),('identity','证件类型及号码'),('address','现住址'),('facts','询问事实及经过')]
EXTRA = {'study':[('school','就读学校'),('course','学习情况')], 'marriage':[('spouse','配偶情况'),('relationship','婚姻情况陈述')], 'residence':[('purpose','居留事由'),('entry','入境及居留情况')], 'violation':[('time_place','相关时间和地点'),('conduct','相关行为陈述')], 'other':[('purpose','询问事项')]}

def schema(scenario):
    return [{'id':key,'label':label} for key,label in COMMON+EXTRA.get(scenario,EXTRA['other'])]

SYSTEM_FIELDS = {'location'} | {f'{prefix}_{part}' for prefix in ('start', 'end') for part in ('year', 'month', 'day', 'hour', 'minute')}

def extraction_groups(template):
    """Keep short identity fields separate from long question/answer fields."""
    fields = [field for field in template['fields'] if field['id'] not in SYSTEM_FIELDS]
    metadata = [field for field in fields if not field['id'].startswith('q')]
    questions = [field for field in fields if field['id'].startswith('q')]
    return [items[i:i+10] for items in (metadata, questions) for i in range(0, len(items), 10)]

def filled_template(template, facts, source_hash):
    allowed={f['id'] for f in template['fields']};values={};sources={}
    for fact in facts:
        key=fact['label']
        if key not in allowed or key in values:
            raise ValueError('模板字段无效或重复')
        if key not in SYSTEM_FIELDS:
            values[key]=fact['value'];sources[key]=fact['segment_ids']
    for key in set(template.get('locked_fields', [])) & allowed:
        values[key] = template.get('values', {}).get(key, '')
        sources.pop(key, None)  # Human corrections are not falsely attributed to AI sources.
    template = template.copy()
    template.pop('dual_prefill', None)
    return {**template,'values':values,'sources':sources,'source_hash':source_hash,'generated':True}

def render(template):
    if template.get('word_asset'):return render_original(template)
    values=template.get('values',{})
    return '\n'.join([template['name'],('示例模板 · 待提供正式模板并人工核对' if template.get('example',True) else '模板字段整理稿 · 待人工核对'),'']+[f"{field['label']}：{values.get(field['id']) or '________（待补充）'}" for field in template['fields']])


CASE_TEMPLATES = [{'id': 'bride', 'name': '询问笔录模板(外籍新娘)', 'filename': '询问笔录模板(外籍新娘).doc', 'scenario': 'marriage', 'version': '1', 'example': False, 'description': '用户提供的笔录模板', 'fields': None}, {'id': 'bride_family', 'name': '询问笔录模板(外籍新娘的家属)', 'filename': '询问笔录模板(外籍新娘的家属).doc', 'scenario': 'marriage', 'version': '1', 'example': False, 'description': '用户提供的笔录模板', 'fields': None}, {'id': 'border_case', 'name': '讯问笔录模板（组织他人偷越国（边）境、提供变造护照）', 'filename': '讯问笔录模板（组织他人偷越国（边）境、提供变造护照）.doc', 'scenario': 'violation', 'version': '1', 'example': False, 'description': '用户提供的笔录模板', 'fields': None}]
for template in CASE_TEMPLATES:
    spec=definition(template['id'])
    template.update(fields=spec['fields'],word_asset=spec['asset'],word_paragraphs=spec['paragraphs'],version='2')
