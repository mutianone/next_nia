"""Idempotent review findings; this module never rewrites speech or QA."""
import re
from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base
from .record_asr_results import fingerprint
from .record_qa_boundaries import question_answer_cuts, question_tail_cut, short_reply_question_cuts


class QAReviewIssue(Base):
    __tablename__='record_qa_review_issues'
    __table_args__=(UniqueConstraint('draft_id','fingerprint'),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    record_id: Mapped[str]=mapped_column(String(36),index=True)
    draft_id: Mapped[str]=mapped_column(String(36),index=True)
    fingerprint: Mapped[str]=mapped_column(String(64))
    data: Mapped[dict]=mapped_column(JSON)
    revision: Mapped[int]=mapped_column(default=1)
    status: Mapped[str]=mapped_column(String(20),default='OPEN')
    resolution: Mapped[dict]=mapped_column(JSON,default=dict)

MESSAGES={
 'CROSS_ROLE_MERGE':('已确认不同角色进入同一讲话轮次。','拆分并重新确认角色'),
 'MULTIPLE_QUESTIONS':('连续独立问题尚未明确回答对应关系。','逐题核对回答范围'),
 'ANSWER_MISPLACEMENT':('回答缺少唯一的前置问题。','核对问题与回答的对应关系'),
 'DUPLICATE_SOURCE':('同一原文范围被重复引用。','保留唯一引用'),
 'UNKNOWN_ROLE':('说话人角色未确认。','确认本次运行的说话人角色'),
 'MIXED_SPEAKER_SUSPECTED':('一段发言存在不同讲话意图的疑点。','回听后按原文范围拆分'),
 'TIMELINE_CONFLICT':('发言时间发生重叠或倒序。','回听并确认边界'),
 'RECOGNITION_VERSION_CONFLICT':('同一音频选择了多个识别版本。','仅选择一个识别版本'),
 'UNANSWERED_QUESTION':('问题没有明确关联的回答。','核对是否缺失回答'),
 'CONFIRMED_CONTENT_CONFLICT':('来源或映射已变化，已确认草稿保持原样。','对比已确认版本后人工处理'),
 'UNKNOWN_INTENT':('讲话意图待确认。','确认提问、回答或其他意图'),
 'CLARIFICATION_REQUIRED':('被询问人提出澄清或反问。','回听并核对后续问答'),
 'THIRD_PARTY':('第三人讲话未作为被询问人回答。','核对插话及后续回答'),
 'NON_QA_SPEECH':('此发言不是已确认的问答。','核对告知或说明内容'),
}


def mixed_boundary_turn_ids(turns, pairs):
    indexed = {turn['id']: turn['text'] for turn in turns}
    mixed = {tid for tid, text in indexed.items() if question_answer_cuts(text) or short_reply_question_cuts(text)}
    for pair in pairs:
        question = ''.join(indexed[tid] for tid in pair['question_turn_ids'])
        for tid in pair['answer_turn_ids']:
            if question_tail_cut(question, indexed[tid]) is not None:
                mixed.add(tid)
    return mixed


def detect(turns,pairs):
    issues={};indexed={t['id']:t for t in turns};used=[];origins={}
    def emit(kind,ids,pair_id=None):
        ids=sorted(set(ids));reason,action=MESSAGES[kind]
        refs=[s for tid in ids if tid in indexed for s in indexed[tid]['sources']]
        key=fingerprint([kind,ids,pair_id])
        issues[key]={'type':kind,'turn_ids':ids,'sentence_ids':sorted(set(s['sentence_id'] for s in refs)),
                     'pair_id':pair_id,'reason':reason,'suggested_action':action}
    for tid in mixed_boundary_turn_ids(turns, pairs):
        emit('MIXED_SPEAKER_SUSPECTED', [tid])
    for t in turns:
        if t['role']=='UNKNOWN':emit('UNKNOWN_ROLE',[t['id']])
        question_hints=list(re.finditer(r'你叫什么|你住在哪里|你什么时候|你是否认识|请介绍一下|说一下你的',t['text']))
        if len(question_hints)>1:emit('MULTIPLE_QUESTIONS',[t['id']])
        if t['intent'] in {'ANSWER', 'CONTINUATION'} and question_hints:
            emit('MIXED_SPEAKER_SUSPECTED', [t['id']])
        roles={s.get('role',t['role']) for s in t['sources']}-{'UNKNOWN'}
        if len(roles)>1:emit('CROSS_ROLE_MERGE',[t['id']])
        if t['role']=='INTERVIEWEE' and t['intent']=='ANSWER' and t.get('suggested_intent') in {'QUESTION','FOLLOW_UP'}:
            emit('MIXED_SPEAKER_SUSPECTED',[t['id']])
        for s in t['sources']:
            origins.setdefault(s['origin_key'],{}).setdefault(s['recognition_run_id'],[]).append(t['id'])
            for old,oid in used:
                if s['sentence_id']==old['sentence_id'] and max(s['start'],old['start'])<min(s['end'],old['end']):emit('DUPLICATE_SOURCE',[oid,t['id']])
                if s['recognition_run_id']==old['recognition_run_id'] and s['channel_id']==old['channel_id'] and s['sentence_id']!=old['sentence_id']:
                    if s['start_ms'] is not None and old['end_ms'] is not None and s['start_ms']<old['end_ms']:
                        emit('TIMELINE_CONFLICT',[oid,t['id']])
                # Only compare absolute offsets within the SAME capture session.
                capture=s['origin_key'].startswith('capture:') and s['origin_key'].split(':')[:2]==old['origin_key'].split(':')[:2]
                if capture and s['recognition_run_id']!=old['recognition_run_id']:
                    if any(max(a['start_ms'],b['start_ms'])<min(a['end_ms'],b['end_ms']) for a in s['source_intervals'] for b in old['source_intervals']):emit('TIMELINE_CONFLICT',[oid,t['id']])
            used.append((s,t['id']))
    for versions in origins.values():
        if len(versions)>1:emit('RECOGNITION_VERSION_CONFLICT',[x for values in versions.values() for x in values])
    qa_used={}
    for p in pairs:
        ids=p['question_turn_ids']+p['answer_turn_ids']+p['clarification_turn_ids']+p['other_turn_ids']
        for reason in set(p['reasons']):emit(reason,ids,p['id'])
        if len(p['question_turn_ids'])>1:emit('MULTIPLE_QUESTIONS',ids,p['id'])
        for tid in p['question_turn_ids']:
            if indexed[tid]['role']!='OFFICER':emit('CROSS_ROLE_MERGE',[tid],p['id'])
        for tid in p['answer_turn_ids']:
            if indexed[tid]['role']!='INTERVIEWEE' or indexed[tid]['intent'] not in {'ANSWER','CONTINUATION'}:emit('ANSWER_MISPLACEMENT',[tid],p['id'])
        for tid in ids:
            if tid in qa_used:emit('DUPLICATE_SOURCE',[tid],p['id'])
            qa_used[tid]=p['id']
    return issues
