"""Authenticated shadow-preview endpoints; optional semantics use existing AI only."""
from typing import Literal
from fastapi import Request,HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import records as r,record_template_fill as fill
from .record_qa_draft_api import Write,change,actor
from .record_fill_candidates import FillBatch
from .record_fill_validation import validate_material
from .record_template_matcher import semantic_suggestions
from .records_ai import RecordAIError

class Preview(Write):
    draft_id:str=Field(max_length=36)
    draft_revision:int=Field(ge=1)
    bindings:dict[str,list[str]]=Field(default_factory=dict,max_length=2000)
    use_semantic:bool=False
    confirmed:bool=False

class Review(Write):
    candidate_revision:int=Field(ge=1)
    action:Literal['ACCEPTED','REJECTED','MODIFIED']
    template_field_id:str|None=Field(default=None,max_length=50)
    value:str|None=Field(default=None,max_length=200000)
    note:str=Field(default='',max_length=1000)

@r.router.post('/{record_id}/fill-preview')
def preview(record_id:str,body:Preview,request:Request):
    actor(request);semantic=None;original=None
    if body.use_semantic:
        settings,_,_=r.confirmed_settings(request,body,'analysis')
        with r.write_session(request.app.state.engine) as s:
            def prepare(record,who):
                d,p,t=validate_material(s,record,body.draft_id,body.draft_revision)
                return p,t,(d.source_hash,t['digest'])
            pairs,template,original=change(s,record_id,body,request,'fill_semantic_requested',prepare)
        try:semantic,_=semantic_suggestions(pairs,template,settings)
        except RecordAIError as e:raise HTTPException(e.status,e.message) from None
        except ValueError as e:raise HTTPException(409,str(e)) from None
    with r.write_session(request.app.state.engine) as s:
        def create(record,who):
            if original:
                d,_,t=validate_material(s,record,body.draft_id,body.draft_revision)
                if original!=(d.source_hash,t['digest']):raise ValueError('语义匹配期间来源发生变化，请重新生成。')
            b=fill.generate(s,record,body.draft_id,body.draft_revision,who,body.bindings,semantic)
            return fill.read_batch(s,record,b)
        return change(s,record_id,body,request,'fill_preview_created',create)

@r.router.get('/{record_id}/fill-candidates')
@r.router.get('/{record_id}/fill-comparison')
def candidates(record_id:str,request:Request,batch_id:str|None=None,draft_id:str|None=None):
    with Session(request.app.state.engine) as s:
        record=r.get_record(s,record_id)
        query=select(FillBatch).where(FillBatch.record_id==record_id)
        if batch_id:query=query.where(FillBatch.id==batch_id)
        if draft_id:query=query.where(FillBatch.draft_id==draft_id)
        batches=list(s.scalars(query.order_by(FillBatch.created_at.desc(),FillBatch.id)))
        return {'batches':[{'id':b.id,'draft_id':b.draft_id,'created_at':b.created_at} for b in batches],
                'batch':fill.read_batch(s,record,batches[0]) if batches else None}

@r.router.post('/{record_id}/fill-candidates/{candidate_id}/review')
def review(record_id:str,candidate_id:str,body:Review,request:Request):
    with r.write_session(request.app.state.engine) as s:
        return change(s,record_id,body,request,'fill_candidate_reviewed',lambda record,who:
            fill.review(s,record,candidate_id,body.candidate_revision,body.action,body.template_field_id,body.value,body.note,who))
