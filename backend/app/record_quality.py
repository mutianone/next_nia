"""Conservative extractive checks, not a semantic truth or legal classifier."""
import re

POLICY = 'extractive-qa-v4'
BOUNDARY = '。！？!?；;\n'
CONDITIONS = re.compile(r'不确定|记不清|记不住|不知道|可能|好像|但是|但|需要核|请.*核|不对|不是|没有|未')

def tokens(text):
    text = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', text)
    return {text[i:i+2] for i in range(len(text)-1)} - {'你是','你的','你有','是否','什么','一下','情况','问你','我们','现在'}

def check_answers(facts, segments, template, semantic_matches=frozenset()):
    indexed = {s['segment_id']:s['text'] for s in segments}
    from .record_selection import source_units
    units=source_units(segments)
    offsets={};cursor=0
    for source in segments:
        offsets[source['segment_id']]=cursor;cursor+=len(source['text'])+1
    fields = {f['id']:f for f in template['fields']}
    accepted, checks = [], []
    for fact in facts:
        field = fields.get(fact['label'], {})
        if not fact['label'].startswith('q'):
            accepted.append(fact)
            continue
        reason = None
        evidence = fact.get('evidence', [])
        spans = []
        if not evidence:
            reason = ('missing_evidence', '缺少逐字问答依据')
        for item in evidence:
            text = indexed.get(item['segment_id'], '')
            question, answer = item['question'], item['answer']
            qid=item.get('question_segment_id') or item['segment_id']
            qtext=indexed.get(qid,'')
            exact=item.get('start') is not None and item.get('end') is not None and item.get('question_start') is not None
            ai=item['start'] if exact else text.find(answer)
            qi=qtext.rfind(question,0,ai) if qid==item['segment_id'] and ai>=0 else qtext.rfind(question)
            if exact: qi=item['question_start']
            if not question or not answer or (exact and (item['end'] != ai+len(answer) or text[ai:item['end']] != answer or qtext[qi:qi+len(question)] != question)) or qi < 0 or ai < 0 or (not exact and text.count(answer) != 1) or item['segment_id'] not in fact['segment_ids'] or qid not in fact['segment_ids']:
                reason = ('invalid_evidence', '引用不是唯一可定位的原文'); break
            answer_roles={u['role'] for u in units if u['segment_id']==item['segment_id'] and u['start']<ai+len(answer) and u['end']>ai}
            question_roles={u['role'] for u in units if u['segment_id']==qid and u['start']<qi+len(question) and u['end']>qi}
            if answer_roles & {'interviewer','interpreter','unconfirmed_voice'} or question_roles & {'interviewee','interpreter','unconfirmed_voice'}:
                reason=('role_mismatch','发言角色未确认或问答角色不匹配'); break
            gap=offsets[item['segment_id']]+ai-(offsets[qid]+qi+len(question))
            if gap < 0 or gap > 12:
                reason = ('unpaired_answer', '问题与回答的位置无法可靠配对'); break
            if re.search(r'说一下你|请(?:你)?介绍|告诉我你|你(?:的)?(?:姓名|国籍|出生日期|联系方式|电话号码).*是什么', answer) or '?' in answer or '？' in answer or re.search(r'(?:^|[\n。])\s*(?:问|民警|询问人)[:：]',answer):
                reason = ('question_in_answer', '回答中混入询问内容'); break
            # Require a complete utterance rather than a substring such as 是真的
            # extracted from 不是真的 / 我认为是真的，但请核查.
            before=text[ai-1:ai];after=text[ai+len(answer):ai+len(answer)+1]
            if (ai and before not in BOUNDARY+'：: \t') or (after and answer[-1] not in BOUNDARY and after not in BOUNDARY):
                reason = ('partial_answer', '引用截断了原句，可能丢失限定条件'); break
            tail=text[ai+len(answer):]
            if tail.startswith(('，','但','不过','不对','只是')):
                reason = ('omitted_condition', '回答之后还有补充或更正'); break
            topics=[field.get('label',''),*field.get('question_aliases',[])]
            followup = bool(spans) and bool(re.match(r'你刚才|刚才说|你的意思|再确认|补充', question))
            if fact['label'] not in semantic_matches and not followup and not any(tokens(question)&tokens(topic) for topic in topics):
                reason = ('question_mismatch', '原问题与模板问题缺少明确匹配，待核对'); break
            if len(answer)>600 and len(answer)>len(text)*.6:
                reason = ('whole_transcript', '单题混入过多转写全文'); break
            spans.append({'segment_id':item['segment_id'],'start':ai,'end':ai+len(answer),'precision':'exact','question':question,'quote':answer})
        if not reason and fact['value'] != '\n'.join(e['answer'] for e in evidence):
            reason = ('rewritten_answer', '答案改写了引用原文，可能强化结论或丢失否认')
        if reason:
            checks.append({'field_id':fact['label'],'code':reason[0],'message':reason[1],'status':'blocked'})
        else:
            accepted.append({**fact,'evidence_spans':spans})
            if fact['label'] in semantic_matches:
                checks.append({'field_id':fact['label'],'code':'semantic_match','message':'同义问答匹配，请核对原文','status':'attention'})
            if CONDITIONS.search(fact['value']):
                checks.append({'field_id':fact['label'],'code':'qualification','message':'含否认、不确定或补充条件，已保留原文','status':'attention'})
    return accepted, checks
