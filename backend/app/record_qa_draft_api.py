"""Local-only review endpoints, using the existing record auth/write lock."""
from typing import Literal
from fastapi import HTTPException,Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import records as r
from . import record_speaker_roles as roles,record_qa_drafts as drafts
from .record_asr_results import RecognitionRun,sentences
from .record_speaker_turns import build_turns
from .record_qa_adhesion import QAReviewIssue

Role=Literal['OFFICER','INTERVIEWEE','OTHER','UNKNOWN']
Intent=Literal['QUESTION','ANSWER','FOLLOW_UP','CONTINUATION','INSTRUCTION','CLARIFICATION','OTHER','UNKNOWN']

class Write(r.Input):
    expected_revision:int=Field(ge=1)

class Mapping(Write):
    recognition_run_id:str=Field(max_length=36)
    channel_id:int|str|None=None
    speaker_id:int|str
    business_role:Role
    mapping_revision:int=Field(ge=0)

class Part(r.Input):
    start:int=Field(ge=0)
    end:int=Field(gt=0)
    role:Role
    intent:Intent

class SentenceReview(Write):
    sentence_revision:int=Field(ge=0)
    parts:list[Part]=Field(min_length=1,max_length=100)

class Rebuild(Write):
    run_ids:list[str]|None=Field(default=None,max_length=100)

class Confirm(Write):
    draft_revision:int=Field(ge=1)

class Resolve(Write):
    issue_revision:int=Field(ge=1)
    note:str=Field(min_length=1,max_length=500)


def actor(request):
    user=getattr(request.state,'user',None)
    if not user or not user.get('id'):raise HTTPException(401,'请登录。')
    if user.get('role') not in {'admin','business'}:raise HTTPException(403,'当前账号没有修改权限。')
    return user['id']


def change(session,record_id,body,request,action,fn):
    who=actor(request);record=r.get_record(session,record_id,True);r.mutable(record,body.expected_revision)
    try:result=fn(record,who)
    except ValueError as e:raise HTTPException(409,str(e)) from None
    r.audit(session,action,record_id=record_id,actor_id=who)
    return result


@r.router.get('/{record_id}/speaker-mappings')
def mappings(record_id:str,request:Request):
    with Session(request.app.state.engine) as s:
        record=r.get_record(s,record_id);items=[];reviews=[]
        runs=list(s.scalars(select(RecognitionRun).where(RecognitionRun.record_id==record_id)))
        for run in runs:
            seen=set()
            for sentence in sentences(s,run):
                d=sentence.data;key=roles.scope(run.id,d.get('channel_id'),d.get('speaker_id'))
                if key not in seen and d.get('speaker_id') is not None:
                    seen.add(key);m=roles.latest_mapping(s,run.id,d.get('channel_id'),d.get('speaker_id'))
                    items.append({'recognition_run_id':run.id,'channel_id':d.get('channel_id'),'speaker_id':d.get('speaker_id'),
                        'business_role':m.business_role if m else 'UNKNOWN','revision':m.revision if m else 0,
                        'confirmed_by':m.confirmed_by if m else None,'confirmed_at':m.confirmed_at if m else None})
                review=roles.latest_review(s,sentence.id);parts,_=roles.sentence_parts(s,sentence)
                reviews.append({'id':sentence.id,'recognition_run_id':run.id,'text':d['text'],'parts':parts,
                    'revision':review.revision if review else 0,'audio_id':run.audio_id,'start_ms':d.get('start_ms'),
                    'channel_id':d.get('channel_id'),'speaker_id':d.get('speaker_id'),
                    **({'local_alternatives': d['local_alternatives']} if d.get('local_alternatives') else {}),
                    'voice_review':bool(d.get('voice_review')), 'listen_start_ms':d.get('listen_start_ms',d.get('start_ms'))})
        history=list(s.scalars(select(roles.SpeakerRoleMapping).where(roles.SpeakerRoleMapping.record_id==record_id).order_by(roles.SpeakerRoleMapping.confirmed_at)))
        sentence_history=list(s.scalars(select(roles.SentenceRoleReview).where(roles.SentenceRoleReview.record_id==record_id).order_by(roles.SentenceRoleReview.confirmed_at)))
        return {'revision':record.revision,'items':items,'sentences':reviews,
                'runs':[{'id':x.id,'origin_key':x.origin_key,'applied':x.applied} for x in runs],
                'history':[{'id':x.id,'recognition_run_id':x.recognition_run_id,'speaker_id':x.speaker_id,'channel_id':x.channel_id,'revision':x.revision,'role':x.business_role,'actor':x.confirmed_by,'at':x.confirmed_at} for x in history],
                'sentence_history':[{'sentence_id':x.sentence_id,'revision':x.revision,'parts':x.parts,'actor':x.confirmed_by,'at':x.confirmed_at} for x in sentence_history]}


@r.router.post('/{record_id}/speaker-mappings')
def save_mapping(record_id:str,body:Mapping,request:Request):
    with r.write_session(request.app.state.engine) as s:
        m=change(s,record_id,body,request,'speaker_role_mapped',lambda record,who:roles.map_role(s,record.id,body.recognition_run_id,body.channel_id,body.speaker_id,body.business_role,body.mapping_revision,who))
        return {'id':m.id,'revision':m.revision}


@r.router.post('/{record_id}/speaker-sentences/{sentence_id}/review')
def save_sentence(record_id:str,sentence_id:str,body:SentenceReview,request:Request):
    with r.write_session(request.app.state.engine) as s:
        row=change(s,record_id,body,request,'speaker_sentence_reviewed',lambda record,who:roles.review_sentence(s,record.id,sentence_id,[p.model_dump() for p in body.parts],body.sentence_revision,who))
        return {'id':row.id,'revision':row.revision}


@r.router.get('/{record_id}/speaker-turns')
def turns(record_id:str,request:Request):
    with Session(request.app.state.engine) as s:
        record=r.get_record(s,record_id)
        try:sources,_,_=drafts.material(s,record)
        except ValueError as e:return {'items':[],'message':str(e)}
        return {'items':build_turns(s,sources)}


@r.router.get('/{record_id}/qa-drafts')
def list_drafts(record_id:str,request:Request):
    with Session(request.app.state.engine) as s:
        record=r.get_record(s,record_id);items=[]
        for d in s.scalars(select(drafts.QADraft).where(drafts.QADraft.record_id==record_id).order_by(drafts.QADraft.version.desc())):
            item=drafts.draft_json(s,d)
            try:item['stale']=drafts.material(s,record,d.run_ids)[1]!=d.source_hash
            except ValueError:item['stale']=True
            items.append(item)
        return {'items':items}


@r.router.post('/{record_id}/qa-drafts/rebuild')
def rebuild(record_id:str,body:Rebuild,request:Request):
    with r.write_session(request.app.state.engine) as s:
        d=change(s,record_id,body,request,'qa_draft_rebuilt',lambda record,who:drafts.rebuild(s,record,who,body.run_ids))
        return drafts.draft_json(s,d)


@r.router.post('/{record_id}/qa-drafts/{draft_id}/confirm')
def confirm(record_id:str,draft_id:str,body:Confirm,request:Request):
    with r.write_session(request.app.state.engine) as s:
        def apply(record,who):
            d=s.get(drafts.QADraft,draft_id)
            if not d or d.record_id!=record_id:raise HTTPException(404,'未找到草稿。')
            return drafts.confirm(s,record,d,body.draft_revision,who)
        d=change(s,record_id,body,request,'qa_draft_confirmed',apply)
        return drafts.draft_json(s,d)


@r.router.get('/{record_id}/qa-review-issues')
def issues(record_id:str,request:Request):
    with Session(request.app.state.engine) as s:
        r.get_record(s,record_id)
        return {'items':[{'id':x.id,'draft_id':x.draft_id,'revision':x.revision,'status':x.status,'resolution':x.resolution,**x.data} for x in s.scalars(select(QAReviewIssue).where(QAReviewIssue.record_id==record_id))]}


@r.router.post('/{record_id}/qa-review-issues/{issue_id}/resolve')
def resolve(record_id:str,issue_id:str,body:Resolve,request:Request):
    with r.write_session(request.app.state.engine) as s:
        def apply(record,who):
            issue=s.get(QAReviewIssue,issue_id)
            if not issue or issue.record_id!=record_id:raise HTTPException(404,'未找到疑点。')
            if issue.revision!=body.issue_revision:raise ValueError('疑点版本冲突，请刷新。')
            if issue.status!='OPEN':raise ValueError('该疑点已处理，保留原处理记录。')
            issue.status='RESOLVED';issue.revision+=1;issue.resolution={'note':body.note.strip(),'actor':who,'at':roles.now()}
            if not issue.resolution['note']:raise ValueError('请填写处理结果。')
            return {'id':issue.id,'revision':issue.revision}
        return change(s,record_id,body,request,'qa_issue_resolved',apply)


class PairEntry(r.Input):
    question_turn_ids:list[str]=Field(default_factory=list,max_length=1)
    answer_turn_ids:list[str]=Field(default_factory=list,max_length=2000)
    other_turn_ids:list[str]=Field(default_factory=list,max_length=2000)
    confirm_cross_run:bool=False
    follow_up_of:int|None=Field(default=None,ge=0)

class PairReview(Confirm):
    pairs:list[PairEntry]=Field(min_length=1,max_length=2000)

@r.router.post('/{record_id}/qa-drafts/{draft_id}/pairs/review')
def review_pairing(record_id:str,draft_id:str,body:PairReview,request:Request):
    with r.write_session(request.app.state.engine) as s:
        def apply(record,who):
            previous=s.get(drafts.QADraft,draft_id)
            if not previous or previous.record_id!=record_id:raise HTTPException(404,'未找到草稿。')
            return drafts.review_pairs(s,record,previous,body.draft_revision,[p.model_dump() for p in body.pairs],who)
        d=change(s,record_id,body,request,'qa_pairing_reviewed',apply)
        return drafts.draft_json(s,d)
