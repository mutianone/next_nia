"""Feature-gated, separately authorized formal application endpoints."""
from fastapi import Request,HTTPException
from pydantic import Field,StrictBool
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import records as r,record_fill_apply as service
from .record_qa_draft_api import actor

class Preview(r.Input):
    fill_batch_id:str=Field(max_length=36)
    candidate_ids:list[str]=Field(min_length=1,max_length=1000)

class Revision(r.Input):
    expected_record_revision:int=Field(ge=1)
    expected_template_revision:str=Field(min_length=64,max_length=64)
    expected_field_revisions:dict[str,int]=Field(min_length=1,max_length=100)
    idempotency_key:str=Field(min_length=8,max_length=100)
    confirmed:StrictBool=False

class Apply(Revision):
    fill_batch_id:str=Field(max_length=36)
    candidate_ids:list[str]=Field(min_length=1,max_length=1000)
    expected_candidate_revisions:dict[str,int]=Field(min_length=1,max_length=1000)

class Restore(Revision):
    reason:str=Field(min_length=1,max_length=1000)

class Setting(r.Input):
    record_structured_fill_apply_enabled:StrictBool
    expected_revision:int=Field(ge=0)

@r.router.get('/_features/structured-fill')
def feature(request:Request):
    if not getattr(request.state,'user',None):raise HTTPException(401,'请登录。')
    with Session(request.app.state.engine) as s:
        state=service.setting(s)
        return {'record_structured_fill_apply_enabled':bool(state and state.enabled),'revision':state.revision if state else 0}

@r.router.put('/_features/structured-fill')
def set_feature(body:Setting,request:Request):
    who=actor(request)
    if request.state.user['role']!='admin':raise HTTPException(403,'仅管理员可修改正式回填开关。')
    with r.write_session(request.app.state.engine) as s:
        state=service.setting(s,True)
        if state.revision!=body.expected_revision:raise HTTPException(409,'开关版本已变化，请刷新。')
        if state.enabled!=body.record_structured_fill_apply_enabled:
            state.enabled=body.record_structured_fill_apply_enabled;state.revision+=1
            r.audit(s,'structured_fill_feature_changed',actor_id=who,enabled=state.enabled,revision=state.revision)
        return {'record_structured_fill_apply_enabled':state.enabled,'revision':state.revision}

@r.router.post('/{record_id}/fill-apply/preview')
def preview(record_id:str,body:Preview,request:Request):
    actor(request)
    with r.write_session(request.app.state.engine) as s:
        record=r.get_record(s,record_id,True);r.mutable(record,record.revision)
        try:service.gate(s);p=service.plan(s,record,body.fill_batch_id,body.candidate_ids)
        except ValueError as e:raise HTTPException(409,str(e)) from None
        p.pop('entries');return p

@r.router.post('/{record_id}/fill-apply')
def apply(record_id:str,body:Apply,request:Request):
    who=actor(request)
    if not body.confirmed:raise HTTPException(400,'请单独确认将候选应用到正式笔录。')
    with r.write_session(request.app.state.engine) as s:
        record=r.get_record(s,record_id,True)
        try:return service.apply(s,record,body,who)
        except ValueError as e:raise HTTPException(409,str(e)) from None

@r.router.get('/{record_id}/fill-apply/history')
def history(record_id:str,request:Request):
    if not getattr(request.state,'user',None):raise HTTPException(401,'请登录。')
    with Session(request.app.state.engine) as s:
        record=r.get_record(s,record_id);items=[]
        for app in s.scalars(select(service.FillApplication).where(service.FillApplication.record_id==record.id).order_by(service.FillApplication.after_revision.desc())):
            data=service.application_json(s,app)
            try:
                r.mutable(record,record.revision);data['restore_preview']=service.restore_plan(s,record,app);data['restore_error']=None
            except (ValueError,HTTPException) as e:data['restore_preview']=None;data['restore_error']=str(e) if isinstance(e,ValueError) else e.detail
            before=service.snapshot(s,record.id,app.before_revision);after=service.snapshot(s,record.id,app.after_revision)
            data['differences']=[{'field_id':f,'before':before.get('values',{}).get(f,''),'after':after.get('values',{}).get(f,'')} for f in data['field_ids']]
            items.append(data)
        return {'record_structured_fill_apply_enabled':service.enabled(s),'items':items}

@r.router.post('/{record_id}/fill-apply/{application_id}/restore')
def restore(record_id:str,application_id:str,body:Restore,request:Request):
    who=actor(request)
    if not body.confirmed:raise HTTPException(400,'请单独确认恢复字段。')
    with r.write_session(request.app.state.engine) as s:
        record=r.get_record(s,record_id,True)
        try:return service.restore(s,record,application_id,body,who)
        except ValueError as e:raise HTTPException(409,str(e)) from None
