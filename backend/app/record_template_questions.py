"""Read template snapshots without changing original Word assets or field IDs."""
from copy import deepcopy
import re
import unicodedata
from .record_asr_results import fingerprint
from .record_templates import SYSTEM_FIELDS


def normalize_question(value):
    # Keep negation, numbers and words. Normalize only spacing/punctuation.
    return re.sub(r'[\W_]+','',unicodedata.normalize('NFKC',value),flags=re.UNICODE)


def parse_template(template):
    if not isinstance(template,dict) or not template.get('id') or not template.get('version'):
        raise ValueError('模板 ID 或版本缺失。')
    fields=template.get('fields')
    if not isinstance(fields,list) or not fields:raise ValueError('模板字段定义为空或无效。')
    result=[];seen=set()
    for source in fields:
        if not isinstance(source,dict):raise ValueError('模板字段必须是对象。')
        fid=source.get('id');label=source.get('label')
        if not isinstance(fid,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,49}',fid) or fid in seen:
            raise ValueError(f'模板字段 ID 无效或重复：{fid!s}')
        if not isinstance(label,str) or not label.strip():raise ValueError(f'字段 {fid} 缺少问题或标签。')
        seen.add(fid)
        for key in ('question_aliases','match_rules'):
            values=source.get(key,[])
            if not isinstance(values,list) or any(not isinstance(x,str) or not x.strip() for x in values):raise ValueError(f'字段 {fid} 的 {key} 无法解析。')
        kind=source.get('kind','fixed_question' if fid.startswith('q') else 'metadata')
        if kind not in {'fixed_question','metadata','free_qa'}:raise ValueError(f'字段 {fid} 类型不支持。')
        if template.get('word_asset'):
            paragraphs=template.get('word_paragraphs',[])
            p,start,end=(source.get(k) for k in ('paragraph','start','end'))
            if any(type(x) is not int for x in (p,start,end)) or not 0<=p<len(paragraphs) or not 0<=start<=end<=len(paragraphs[p]):raise ValueError(f'字段 {fid} 的 Word 原文范围无效。')
        for key in ('allow_shared_source','allow_multiple'):
            if key in source and type(source[key]) is not bool:raise ValueError(f'字段 {fid} 的 {key} 必须为布尔值。')
        group=source.get('exclusive_group')
        if group is not None and (not isinstance(group,str) or not group):raise ValueError(f'字段 {fid} 的互斥组无效。')
        result.append({'id':fid,'label':label,'kind':kind,'question_aliases':source.get('question_aliases',[]),
            'match_rules':source.get('match_rules',[]),'allow_shared_source':source.get('allow_shared_source',False),
            'allow_multiple':source.get('allow_multiple',False),'exclusive_group':group,
            'fillable':fid not in SYSTEM_FIELDS,'protected':fid in template.get('locked_fields',[])})
    return {'id':template['id'],'version':str(template['version']),'fields':result,'snapshot':deepcopy(template),'digest':fingerprint(template)}
