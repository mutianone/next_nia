"""Validate confirmed structured QA, never infer roles from raw ASR text."""
from sqlalchemy import select
from .record_qa_drafts import QADraft,material
from .record_speaker_turns import SpeakerTurn,build_turns
from .record_qa_state_machine import QAPair
from .record_qa_adhesion import QAReviewIssue
from .record_template_questions import parse_template

def validate_material(session,record,draft_id,revision):
    draft=session.get(QADraft,draft_id)
    if not draft or draft.record_id!=record.id:raise ValueError('QA 草稿不存在。')
    if draft.status!='CONFIRMED' or draft.revision!=revision:raise ValueError('请使用已确认的当前 QA 草稿版本。')
    runs,digest,_=material(session,record,draft.run_ids)
    if digest!=draft.source_hash:raise ValueError('草稿来源已变化，请重新核对。')
    if session.scalar(select(QAReviewIssue.id).where(QAReviewIssue.draft_id==draft.id,QAReviewIssue.status=='OPEN')):raise ValueError('仍有未处理的 QA 疑点。')
    originals={s.id:(run,s) for run,ss in runs for s in ss}
    canonical={(ref['sentence_id'],ref['start'],ref['end']):ref for turn in build_turns(session,runs) for ref in turn['sources']}
    turns={t.data['id']:t.data for t in session.scalars(select(SpeakerTurn).where(SpeakerTurn.draft_id==draft.id))}
    pairs=[];used_turns=set();ranges={}
    for row in session.scalars(select(QAPair).where(QAPair.draft_id==draft.id).order_by(QAPair.position)):
        p=row.data
        # Non-QA statements remain visible in the draft; they cannot fill a field.
        if not p['question_turn_ids'] and not p['answer_turn_ids']:continue
        if p['state']!='QA_COMPLETED' or len(p['question_turn_ids'])!=1 or not p['answer_turn_ids'] or p.get('clarification_turn_ids') or p.get('other_turn_ids'):raise ValueError('问答尚不完整，不能生成回填候选。')
        refs=[];texts={}
        for kind,role,intents in [('question','OFFICER',{'QUESTION','FOLLOW_UP'}),('answer','INTERVIEWEE',{'ANSWER','CONTINUATION'})]:
            content=[]
            for tid in p[f'{kind}_turn_ids']:
                t=turns.get(tid)
                if tid in used_turns or not t or t['status']!='CONFIRMED' or t['role']!=role or t['intent'] not in intents:raise ValueError('问答角色、意图或来源引用冲突。')
                used_turns.add(tid);pieces=[]
                if not t['sources']:raise ValueError('讲话轮次缺少来源。')
                for ref in t['sources']:
                    original=originals.get(ref['sentence_id'])
                    if not original:raise ValueError('来源句子不存在。')
                    run,sentence=original;d=sentence.data;a=ref['start'];b=ref['end']
                    if type(a) is not int or type(b) is not int or not 0<=a<b<=len(d['text']):raise ValueError('来源原文范围无效。')
                    if ref['recognition_run_id']!=run.id or ref.get('audio_id')!=run.audio_id or ref.get('speaker_id')!=d.get('speaker_id') or ref.get('channel_id')!=d.get('channel_id') or ref.get('role')!=role:raise ValueError('音频来源或角色引用不一致。')
                    if canonical.get((sentence.id,a,b))!=ref:raise ValueError('来源时间轴、角色或原文范围与已确认记录不一致。')
                    existing=ranges.setdefault(sentence.id,[])
                    if any(a<end and b>start for start,end in existing):raise ValueError('来源原文被重复引用。')
                    existing.append((a,b));pieces.append(d['text'][a:b]);refs.append(ref)
                if '\n'.join(pieces)!=t['text']:raise ValueError('讲话轮次原文范围不完整。')
                content.append(t['text'])
            texts[kind]='\n'.join(content)
            if texts[kind]!=p[kind]:raise ValueError('问答原文发生丢失或改写。')
        if refs!=p['sources']:raise ValueError('问答来源引用不完整。')
        pairs.append({'qa_pair_id':row.id,**texts,'sources':refs,'follow_up_of':p.get('follow_up_of')})
    if not pairs:raise ValueError('暂无完整、已确认的问答可匹配。')
    return draft,pairs,parse_template(record.template_snapshot)
