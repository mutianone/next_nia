"""Versioned sidecar drafts. No writes to record_segments or template values."""
from uuid import uuid4,uuid5,NAMESPACE_URL
from sqlalchemy import JSON,String,UniqueConstraint,select
from sqlalchemy.orm import Mapped,mapped_column
from .database import Base
from .record_asr_results import RecognitionRun,RecognitionSentence,sentences,fingerprint
from .record_speaker_roles import sentence_parts,now
from .record_speaker_turns import SpeakerTurn,build_turns
from .record_qa_state_machine import QAPair,pair_turns
from .record_qa_adhesion import QAReviewIssue,detect,MESSAGES,mixed_boundary_turn_ids


class QADraft(Base):
    __tablename__='record_qa_drafts'
    __table_args__=(UniqueConstraint('record_id','input_hash'),UniqueConstraint('record_id','version'))
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    record_id: Mapped[str]=mapped_column(String(36),index=True)
    version: Mapped[int]=mapped_column()
    input_hash: Mapped[str]=mapped_column(String(64))
    source_hash: Mapped[str]=mapped_column(String(64))
    run_ids: Mapped[list]=mapped_column(JSON)
    revision: Mapped[int]=mapped_column(default=1)
    status: Mapped[str]=mapped_column(String(20),default='PROVISIONAL')
    created_by: Mapped[str]=mapped_column(String(100))
    created_at: Mapped[str]=mapped_column(String(40))
    confirmed_by: Mapped[str|None]=mapped_column(String(100),nullable=True)
    confirmed_at: Mapped[str|None]=mapped_column(String(40),nullable=True)


def material(session,record,run_ids=None):
    from .records import RecordSegment,AudioClip
    available=list(session.scalars(select(RecognitionRun).where(RecognitionRun.record_id==record.id)))
    indexed={r.id:r for r in available}
    body=list(session.scalars(select(RecordSegment).where(RecordSegment.record_id==record.id).order_by(RecordSegment.position)))
    if run_ids is None:
        rows=[r for r in available if r.applied]
    else:
        if len(run_ids)!=len(set(run_ids)) or any(x not in indexed for x in run_ids):raise ValueError('识别版本重复或不属于当前笔录。')
        rows=[indexed[x] for x in run_ids]
    # Version selection cannot reorder the original recording timeline.
    positions={s.id:s.position for s in body};origin_positions={}
    audio_order={a.id:i for i,a in enumerate(session.scalars(select(AudioClip).where(AudioClip.record_id==record.id).order_by(AudioClip.created_at,AudioClip.id)))}
    for run in available:
        position=min((positions.get(s.id,10**9) for s in sentences(session,run)),default=10**9)
        position=min(position,10**6+audio_order.get(run.audio_id,10**6))
        origin_positions[run.origin_key]=min(position,origin_positions.get(run.origin_key,10**9))
    rows.sort(key=lambda run:(origin_positions[run.origin_key],run.interval_map[0]['session_start_sample']/run.interval_map[0]['sample_rate'] if run.interval_map else 0,run.id))
    if not rows:raise ValueError('暂无逐句识别来源；历史文字请继续使用原有人工审核。')
    origins=[r.origin_key for r in rows]
    if len(origins)!=len(set(origins)):raise ValueError('同一音频只能选择一个识别版本。')
    sources=[(r,sentences(session,r)) for r in rows]
    if any(r.provider == 'dual' for r, _ in sources):
        raise ValueError('请先完成双引擎核对，再确认角色并构建草稿。')
    if sum(len(ss) for _,ss in sources)>2000:raise ValueError('单次草稿最多支持2000个识别句子。')
    digest=fingerprint({'schema':2,'runs':[(r.id,r.digest,[(s.id,sentence_parts(session,s)) for s in ss]) for r,ss in sources],
                        'body':[(s.id,s.text,s.speaker,s.start_ms,s.end_ms) for s in body]})
    return sources,digest,body


def boundary_review_key(source_key, turns, pairs):
    # Updated boundaries need a new immutable draft, even for the same ASR.
    question_sources = [[(s['sentence_id'], s['start'], s['end']) for s in t['sources']]
                        for t in turns if t['role']=='OFFICER' and t['intent'] in {'QUESTION','FOLLOW_UP'} and len(t['sources'])>1]
    if question_sources:
        source_key = fingerprint([source_key, 'question-continuations', question_sources])
    mixed = sorted(mixed_boundary_turn_ids(turns, pairs))
    return fingerprint([source_key, 'mixed-question-answer', mixed]) if mixed else source_key


def rebuild(session,record,actor,run_ids=None):
    sources,digest,body=material(session,record,run_ids)
    turns=build_turns(session,sources);pairs=pair_turns(turns)
    input_key = boundary_review_key(digest, turns, pairs)
    old=session.scalar(select(QADraft).where(QADraft.record_id==record.id,QADraft.input_hash==input_key))
    if old:return old
    latest=session.scalar(select(QADraft).where(QADraft.record_id==record.id).order_by(QADraft.version.desc()))
    draft=QADraft(id=str(uuid4()),record_id=record.id,version=(latest.version+1 if latest else 1),input_hash=input_key,source_hash=digest,
                  run_ids=[r.id for r,_ in sources],created_by=actor,created_at=now())
    session.add(draft);session.flush()
    issues=detect(turns,pairs)
    confirmed=session.scalar(select(QADraft.id).where(QADraft.record_id==record.id,QADraft.status=='CONFIRMED'))
    original={s.id:s.data['text'] for _,ss in sources for s in ss}
    edited=[s.id for s in body if s.id in original and s.text!=original[s.id]]
    if confirmed or edited:
        key=fingerprint(['CONFIRMED_CONTENT_CONFLICT',confirmed,edited]);reason,action=MESSAGES['CONFIRMED_CONTENT_CONFLICT']
        issues[key]={'type':'CONFIRMED_CONTENT_CONFLICT','turn_ids':[],'sentence_ids':edited,'pair_id':None,
                     'reason':reason,'suggested_action':action,'confirmed_draft_id':confirmed}
    for i,t in enumerate(turns):session.add(SpeakerTurn(id=str(uuid5(NAMESPACE_URL,f'{draft.id}:turn:{t["id"]}')),record_id=record.id,draft_id=draft.id,position=i,data=t))
    # Project ONLY structured pairs, retaining exact turn references and text.
    indexed={t['id']:t for t in turns}
    for i,p in enumerate(pairs):
        p={**p,'question':'\n'.join(indexed[x]['text'] for x in p['question_turn_ids']),
           'answer':'\n'.join(indexed[x]['text'] for x in p['answer_turn_ids']),
           'sources':[s for key in ('question_turn_ids','answer_turn_ids','clarification_turn_ids','other_turn_ids') for tid in p[key] for s in indexed[tid]['sources']]}
        session.add(QAPair(id=f'{draft.id}:{i}',record_id=record.id,draft_id=draft.id,position=i,data=p))
    for key,data in issues.items():session.add(QAReviewIssue(id=str(uuid5(NAMESPACE_URL,f'{draft.id}:{key}')),record_id=record.id,draft_id=draft.id,fingerprint=key,data=data))
    if issues:draft.status='REVIEW_REQUIRED'
    session.flush();return draft


def draft_json(session,draft):
    turns=list(session.scalars(select(SpeakerTurn).where(SpeakerTurn.draft_id==draft.id).order_by(SpeakerTurn.position)))
    pairs=list(session.scalars(select(QAPair).where(QAPair.draft_id==draft.id).order_by(QAPair.position)))
    return {k:getattr(draft,k) for k in ('id','version','revision','status','run_ids','created_by','created_at','confirmed_by','confirmed_at')} | {
        'turns':[x.data for x in turns],'pairs':[x.data for x in pairs]}


def require_boundary_review(session, draft, turns, pairs):
    mixed = mixed_boundary_turn_ids(turns, pairs)
    if not mixed:
        return
    required = {fingerprint(['MIXED_SPEAKER_SUSPECTED', [tid], None]) for tid in mixed}
    resolved = set(session.scalars(select(QAReviewIssue.fingerprint).where(
        QAReviewIssue.draft_id == draft.id, QAReviewIssue.status == 'RESOLVED')))
    if not required.issubset(resolved):
        raise ValueError('发现未经核对的问答粘连，请重建草稿并回听确认发言边界。')


def confirm(session,record,draft,expected,actor):
    if draft.revision!=expected:raise ValueError('草稿版本冲突，请刷新。')
    _,digest,_=material(session,record,draft.run_ids)
    if digest!=draft.source_hash:raise ValueError('来源或角色已变化，请重新构建并核对。')
    if session.scalar(select(QAReviewIssue.id).where(QAReviewIssue.draft_id==draft.id,QAReviewIssue.status=='OPEN')):raise ValueError('请先处理草稿疑点。')
    # Resolution notes never promote UNKNOWN identity/intent to confirmed content.
    turns=list(session.scalars(select(SpeakerTurn).where(SpeakerTurn.draft_id==draft.id)))
    pairs = session.scalars(select(QAPair).where(QAPair.draft_id == draft.id))
    require_boundary_review(session, draft, (t.data for t in turns), (p.data for p in pairs))
    if draft.status=='CONFIRMED':return draft
    if any(t.data['status']!='CONFIRMED' for t in turns):raise ValueError('请先确认发言角色和意图，并重建草稿。')
    draft.status='CONFIRMED';draft.revision+=1;draft.confirmed_by=actor;draft.confirmed_at=now()
    session.flush();return draft


def purge_audio(session,record_id,audio_id):
    """Called only by the existing explicitly requested audio-deletion operation."""
    from .record_speaker_roles import SpeakerRoleMapping,SentenceRoleReview
    run_ids=list(session.scalars(select(RecognitionRun.id).where(RecognitionRun.record_id==record_id,RecognitionRun.audio_id==audio_id)))
    sentence_ids=list(session.scalars(select(RecognitionSentence.id).where(RecognitionSentence.run_id.in_(run_ids))))
    for draft in session.scalars(select(QADraft).where(QADraft.record_id==record_id)):
        if set(run_ids)&set(draft.run_ids):
            from .record_template_fill import purge_draft
            purge_draft(session,draft.id)
            for model in (QAReviewIssue,QAPair,SpeakerTurn):
                for row in session.scalars(select(model).where(model.draft_id==draft.id)):session.delete(row)
            session.delete(draft)
    for row in session.scalars(select(SpeakerRoleMapping).where(SpeakerRoleMapping.recognition_run_id.in_(run_ids))):session.delete(row)
    for row in session.scalars(select(SentenceRoleReview).where(SentenceRoleReview.sentence_id.in_(sentence_ids))):session.delete(row)
    session.flush()


def review_pairs(session,record,previous,expected,entries,actor):
    """Manual links produce a new draft; confirmed drafts remain immutable."""
    if previous.revision!=expected:raise ValueError('草稿版本冲突，请刷新。')
    _,source_hash,_=material(session,record,previous.run_ids)
    if source_hash!=previous.source_hash:raise ValueError('来源已变化，请先重新构建。')
    turns=[x.data for x in session.scalars(select(SpeakerTurn).where(SpeakerTurn.draft_id==previous.id).order_by(SpeakerTurn.position))]
    indexed={t['id']:t for t in turns};seen=set();pairs=[]
    for i,entry in enumerate(entries):
        ids=entry['question_turn_ids']+entry['answer_turn_ids']+entry['other_turn_ids']
        if not ids or len(ids)!=len(set(ids)) or any(x not in indexed or x in seen for x in ids):raise ValueError('每个讲话轮次必须且只能关联一次。')
        seen.update(ids)
        if len(entry['question_turn_ids'])>1:raise ValueError('独立问题不能合并为同一问题。')
        for tid in entry['question_turn_ids']:
            if indexed[tid]['role']!='OFFICER' or indexed[tid]['intent'] not in {'QUESTION','FOLLOW_UP'}:raise ValueError('问题需先确认民警角色及提问意图。')
        for tid in entry['answer_turn_ids']:
            if indexed[tid]['role']!='INTERVIEWEE' or indexed[tid]['intent'] not in {'ANSWER','CONTINUATION'}:raise ValueError('回答需先确认被询问人角色及回答意图。')
        if entry['answer_turn_ids'] and not entry['question_turn_ids']:raise ValueError('请指定回答对应的问题。')
        positions={t['id']:i for i,t in enumerate(turns)}
        answer_positions=[positions[t] for t in entry['answer_turn_ids']]
        if answer_positions!=sorted(answer_positions):raise ValueError('回答必须按原发言顺序关联。')
        if answer_positions and min(answer_positions)<=positions[entry['question_turn_ids'][0]]:raise ValueError('不能将问题之前的发言直接作为回答。')
        runs={s['recognition_run_id'] for tid in ids for s in indexed[tid]['sources']}
        if len(runs)>1 and not entry['confirm_cross_run']:raise ValueError('跨识别批次关联必须人工明确确认。')
        follow=entry.get('follow_up_of')
        if follow is not None and (follow<0 or follow>=i):raise ValueError('追问必须关联前面的问答。')
        p={k:entry[k] for k in ('question_turn_ids','answer_turn_ids','other_turn_ids')}
        p.update(id=f'qa-{i+1}',clarification_turn_ids=[],follow_up_of=f'qa-{follow+1}' if follow is not None else None,
                 reasons=[],state='QA_COMPLETED' if p['answer_turn_ids'] else 'REVIEW_REQUIRED',state_history=['REVIEW_REQUIRED','QA_COMPLETED'] if p['answer_turn_ids'] else ['REVIEW_REQUIRED'],manual_pairing=True,confirmed_cross_run=entry['confirm_cross_run'])
        if p['question_turn_ids'] and not p['answer_turn_ids']:p['reasons'].append('UNANSWERED_QUESTION')
        if p['other_turn_ids']:p['reasons'].append('NON_QA_SPEECH')
        p['question']='\n'.join(indexed[t]['text'] for t in p['question_turn_ids'])
        p['answer']='\n'.join(indexed[t]['text'] for t in p['answer_turn_ids'])
        p['sources']=[s for tid in ids for s in indexed[tid]['sources']]
        pairs.append(p)
    if seen!=set(indexed):raise ValueError('仍有讲话轮次未处理，不能遗漏原文。')
    digest=boundary_review_key(fingerprint([source_hash,entries]), turns, pairs)
    old=session.scalar(select(QADraft).where(QADraft.record_id==record.id,QADraft.input_hash==digest))
    if old:return old
    latest=session.scalar(select(QADraft).where(QADraft.record_id==record.id).order_by(QADraft.version.desc()))
    draft=QADraft(id=str(uuid4()),record_id=record.id,version=latest.version+1,input_hash=digest,source_hash=source_hash,run_ids=previous.run_ids,created_by=actor,created_at=now())
    session.add(draft);session.flush()
    for i,t in enumerate(turns):session.add(SpeakerTurn(id=str(uuid5(NAMESPACE_URL,f'{draft.id}:{t["id"]}')),record_id=record.id,draft_id=draft.id,position=i,data=t))
    for i,p in enumerate(pairs):session.add(QAPair(id=f'{draft.id}:{i}',record_id=record.id,draft_id=draft.id,position=i,data=p))
    findings=detect(turns,pairs)
    # Manual association cannot erase source conflicts, including earlier edits.
    for issue in session.scalars(select(QAReviewIssue).where(QAReviewIssue.draft_id==previous.id)):
        if issue.data['type']=='CONFIRMED_CONTENT_CONFLICT':findings[issue.fingerprint]=issue.data
    for key,data in findings.items():session.add(QAReviewIssue(id=str(uuid5(NAMESPACE_URL,f'{draft.id}:{key}')),record_id=record.id,draft_id=draft.id,fingerprint=key,data=data))
    if findings:draft.status='REVIEW_REQUIRED'
    session.flush();return draft
