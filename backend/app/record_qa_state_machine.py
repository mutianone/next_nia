"""Independent QA candidates. Roles and speech intent have separate evidence."""
from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class QAPair(Base):
    __tablename__ = 'record_qa_draft_pairs'
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(36), index=True)
    draft_id: Mapped[str] = mapped_column(String(36), index=True)
    position: Mapped[int] = mapped_column()
    data: Mapped[dict] = mapped_column(JSON)


STATES={'WAITING_QUESTION','QUESTION_OPEN','WAITING_ANSWER','ANSWER_OPEN','FOLLOW_UP','REVIEW_REQUIRED','QA_COMPLETED'}


def pair_turns(turns):
    pairs=[];pending=[];active=None;run=None;last_answered=None
    def add(question=None,turn=None,reason=None,follow=None):
        p={'id':f'qa-{len(pairs)+1}','question_turn_ids':[question['id']] if question else [],
           'answer_turn_ids':[],'clarification_turn_ids':[], 'other_turn_ids':[turn['id']] if turn else [],
           'follow_up_of':follow,'reasons':[reason] if reason else [], 'state':'WAITING_ANSWER' if question else 'REVIEW_REQUIRED',
           'state_history':['WAITING_QUESTION','QUESTION_OPEN','WAITING_ANSWER'] if question else ['WAITING_QUESTION','REVIEW_REQUIRED']}
        pairs.append(p);return p
    for t in turns:
        current_run=t['sources'][0]['recognition_run_id']
        if current_run!=run:
            pending=[];active=None;last_answered=None;run=current_run
        role=t['role'];intent=t['intent']
        if role=='UNKNOWN' or intent=='UNKNOWN':
            add(turn=t,reason='UNKNOWN_ROLE' if role=='UNKNOWN' else 'UNKNOWN_INTENT')
            pending=[];active=None;last_answered=None
            continue
        if role=='OFFICER' and intent in {'QUESTION','FOLLOW_UP'}:
            follow=last_answered['id'] if intent=='FOLLOW_UP' and last_answered else None
            active=add(question=t,follow=follow)
            if intent=='FOLLOW_UP':active['state_history'].append('FOLLOW_UP')
            pending.append(active)
        elif role=='INTERVIEWEE' and intent in {'ANSWER','CONTINUATION'}:
            if len(pending)>1:
                orphan=add(turn=t,reason='ANSWER_MISPLACEMENT')
                orphan['candidate_question_ids']=[p['id'] for p in pending]
                for p in pending:p['reasons'].append('MULTIPLE_QUESTIONS')
                pending=[];active=None;last_answered=None
            elif pending or (active and active['answer_turn_ids']):
                active=pending.pop() if pending else active
                active['answer_turn_ids'].append(t['id'])
                active['state']='QA_COMPLETED';active['state_history']+=['ANSWER_OPEN','QA_COMPLETED']
                last_answered=active
            else:add(turn=t,reason='ANSWER_MISPLACEMENT')
        elif role=='INTERVIEWEE' and intent in {'CLARIFICATION','QUESTION'} and active and len(pending)<=1:
            # A counterquestion remains the interviewee's speech; not an answer.
            active['clarification_turn_ids'].append(t['id'])
            active['reasons'].append('CLARIFICATION_REQUIRED')
        else:
            add(turn=t,reason='THIRD_PARTY' if role=='OTHER' else 'NON_QA_SPEECH')
            # Interruption/instruction breaks automatic answer attachment.
            pending=[];active=None;last_answered=None
    for p in pairs:
        if p['question_turn_ids'] and not p['answer_turn_ids']:p['reasons'].append('UNANSWERED_QUESTION')
        if p['reasons']:p['state']='REVIEW_REQUIRED';p['state_history'].append('REVIEW_REQUIRED')
    return pairs
