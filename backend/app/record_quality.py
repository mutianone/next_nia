"""Conservative extractive checks, not a semantic truth or legal classifier."""
import re

from .record_selection import QUOTED_SPEECH, officer_question_contexts, repeated_particle_cut, source_units
from .record_qa_boundaries import contains_question_prompt, question_answer_cuts, question_tail_cut, short_reply_question_cuts

POLICY = 'extractive-qa-v7'
BOUNDARY = '。！？!?；;\n'
CONDITIONS = re.compile(r'不确定|记不清|记不住|不知道|可能|好像|但是|但|需要核|请.*核|不对|不是|没有|未')

def tokens(text):
    text = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', text)
    return {text[i:i+2] for i in range(len(text)-1)} - {'你是','你的','你有','是否','什么','一下','情况','问你','我们','现在'}

def mixed_answer(question, answer):
    second_person_question = re.search(
        r'(?:^|[。！？?!；;\n])\s*(?:你|您)(?:是|的)?[^，。！？?!；;\n]{0,16}'
        r'(?:什么时候|什么地方|什么名字|为什么|从哪|在哪里|去哪|怎么来|多少次|几次)', answer)
    return bool(second_person_question or question_answer_cuts(answer) or short_reply_question_cuts(answer) or question_tail_cut(question, answer) is not None
                or re.search(r'说一下你|请(?:你)?介绍|告诉我你|你(?:的)?(?:姓名|国籍|出生日期|联系方式|电话号码).*是什么', answer)
                or re.search(r'[?？]|[吗嗎][。！!\s]*$|(?:^|[\s。；，])(?:问|民警|询问人)\s*[:：]', answer))


def adjacent_evidence(question, answer, gap, confirmed_pair):
    # A short skipped phrase can be an entire second question or a negation.
    gap = re.sub(r'^[\s。！？?!；;，,]*(?:(?:答|被询问人|当事人)\s*[:：]\s*)?', '', gap)
    if not gap:
        return True
    return confirmed_pair and repeated_particle_cut(question, gap + answer) == len(gap)


def check_answers(facts, segments, template, semantic_matches=frozenset()):
    indexed = {s['segment_id']:s['text'] for s in segments}
    units=source_units(segments)
    contexts=officer_question_contexts(segments)
    raw_asr = {s['segment_id'] for s in segments if s.get('recognition_run_id') and
               s.get('speaker', 'unknown') == 'unknown'}
    disputed = {s['segment_id'] for s in segments if s.get('voice_review') and s.get('speaker','unknown') == 'unknown'}
    quotes = {sid: [m.span() for m in QUOTED_SPEECH.finditer(text)] for sid, text in indexed.items()}
    offsets={};cursor=0
    for source in segments:
        offsets[source['segment_id']]=cursor;cursor+=len(source['text'])+1
    fields = {f['id']:f for f in template['fields']}
    accepted, checks = [], []
    for fact in facts:
        field = fields.get(fact['label'], {})
        if not fact['label'].startswith('q'):
            if disputed.intersection(fact.get('segment_ids', [])):
                checks.append({'field_id':fact['label'], 'code':'voice_boundary_review', 'status':'blocked',
                               'message':'字词与声音边界需要回听核对，暂不回填'})
                continue
            accepted.append(fact)
            continue
        reason = None
        evidence = fact.get('evidence', [])
        spans = []
        context_ids = set()
        if not evidence:
            reason = ('missing_evidence', '缺少逐字问答依据')
        for item in evidence:
            text = indexed.get(item['segment_id'], '')
            question, answer = item['question'], item['answer']
            qid=item.get('question_segment_id') or item['segment_id']
            if qid in disputed or item['segment_id'] in disputed:
                reason = ('voice_boundary_review', '字词与声音边界需要回听核对，暂不回填'); break
            qtext=indexed.get(qid,'')
            exact=item.get('start') is not None and item.get('end') is not None and item.get('question_start') is not None
            ai=item['start'] if exact else text.find(answer)
            qi=qtext.rfind(question,0,ai) if qid==item['segment_id'] and ai>=0 else qtext.rfind(question)
            if exact: qi=item['question_start']
            if not question or not answer or (exact and (item['end'] != ai+len(answer) or text[ai:item['end']] != answer or qtext[qi:qi+len(question)] != question)) or qi < 0 or ai < 0 or (not exact and text.count(answer) != 1) or item['segment_id'] not in fact['segment_ids'] or qid not in fact['segment_ids']:
                reason = ('invalid_evidence', '引用不是唯一可定位的原文'); break
            answer_units=[u for u in units if u['segment_id']==item['segment_id'] and u['start']<ai+len(answer) and u['end']>ai]
            answer_roles={u['role'] for u in answer_units}
            question_roles={u['role'] for u in units if u['segment_id']==qid and u['start']<qi+len(question) and u['end']>qi}
            quoted_question = any(start <= qi + len(question) - 1 < end for start, end in quotes.get(qid, []))
            if (not answer_roles or not question_roles or quoted_question
                    or answer_roles & {'interviewer','interpreter','unconfirmed_voice'}
                    or question_roles & {'interviewee','interpreter','unconfirmed_voice'}):
                reason=('role_mismatch','发言角色未确认或问答角色不匹配'); break
            gap=offsets[item['segment_id']]+ai-(offsets[qid]+qi+len(question))
            confirmed = answer_roles == {'interviewee'} and question_roles == {'interviewer'}
            if answer_roles != {'interviewee'} and contains_question_prompt(answer):
                reason = ('question_in_answer', '疑似将提问人的补充说明纳入回答，请回听核对'); break
            skipped = (qtext[qi+len(question):ai] if qid == item['segment_id'] else
                       qtext[qi+len(question):] + '\n' + text[:ai])
            if (gap < 0 or gap != len(skipped) or
                    not adjacent_evidence(question, answer, skipped, confirmed)):
                reason = ('unpaired_answer', '问题与回答的位置无法可靠配对'); break
            context = contexts.get(qid)
            question_sources = []
            if context is not None:
                if not context or not confirmed or question.strip() != qtext.strip():
                    reason = ('unpaired_answer', '连续问话与回答的对应关系需要核对'); break
                question = '\n'.join(s['text'] for s in context)
                question_sources = [{'segment_id':s['segment_id'],'start':0,'end':len(s['text']),'precision':'exact'} for s in context]
                context_ids.update(s['segment_id'] for s in context)
            if item['segment_id'] in raw_asr and not confirmed:
                reply = re.sub(r'[\W_]+', '', answer)
                preceding = qtext[:qi].rstrip()
                if answer_roles != {'interviewee'} and len(answer_units)>1:
                    reason = ('unverified_asr_answer', '连续数句的发言人尚未核对，可能含提问人的补充说明'); break
                # With no verified speaker, a missing syllable can reverse a
                # yes/no reply, and the next question can be joined to it.
                if ((len(reply) <= 8 and re.search(r'不|没|否|未|对|是|确定|肯定|^有|^嗯', reply))
                        or preceding.endswith(('？', '?'))):
                    reason = ('unverified_asr_answer', '原始转写的短答或连续问话尚未确认说话人，请回听并核对问答'); break
            if mixed_answer(question, answer):
                reason = ('question_in_answer', '回答中混入询问内容'); break
            # Require a complete utterance rather than a substring such as 是真的
            # extracted from 不是真的 / 我认为是真的，但请核查.
            before=text[ai-1:ai];after=text[ai+len(answer):ai+len(answer)+1]
            starts_turn = confirmed and any(u['segment_id'] == item['segment_id'] and
                                            u['role'] == 'interviewee' and u['start'] == ai for u in units)
            if (ai and not starts_turn and before not in BOUNDARY+'：: \t') or (after and answer[-1] not in BOUNDARY and after not in BOUNDARY):
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
            spans.append({'segment_id':item['segment_id'],'start':ai,'end':ai+len(answer),'precision':'exact','question':question,'quote':answer,
                          **({'question_sources':question_sources} if question_sources else {})})
        if not reason and fact['value'] != '\n'.join(e['answer'] for e in evidence):
            reason = ('rewritten_answer', '答案改写了引用原文，可能强化结论或丢失否认')
        if reason:
            checks.append({'field_id':fact['label'],'code':reason[0],'message':reason[1],'status':'blocked'})
        else:
            if context_ids:
                context_ids.update(fact['segment_ids'])
                fact = {**fact,'segment_ids':[sid for sid in indexed if sid in context_ids]}
            accepted.append({**fact,'evidence_spans':spans})
            if fact['label'] in semantic_matches:
                checks.append({'field_id':fact['label'],'code':'semantic_match','message':'同义问答匹配，请核对原文','status':'attention'})
            if CONDITIONS.search(fact['value']):
                checks.append({'field_id':fact['label'],'code':'qualification','message':'含否认、不确定或补充条件，已保留原文','status':'attention'})
    return accepted, checks
