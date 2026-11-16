"""Append-only template configuration; sessions retain their creation snapshot."""
from copy import deepcopy
from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import records as r

router=APIRouter(prefix='/api/record-templates',tags=['笔录模板配置'],route_class=r.RecordsRoute)


def current(session, identifier):
    return session.scalar(select(r.RecordTemplateRevision).where(r.RecordTemplateRevision.template_id==identifier).order_by(r.RecordTemplateRevision.revision.desc()).limit(1))


def configured(session,identifier):
    saved=current(session,identifier)
    if saved:return deepcopy(saved.definition)
    original=next((t for t in r.ALL_TEMPLATES if t['id']==identifier),None)
    if original:return {**deepcopy(original),'config_revision':0,'enabled':True}
    raise HTTPException(404,'笔录模板不存在。')


def available(session,include_disabled=False):
    ids=[t['id'] for t in r.CASE_TEMPLATES]
    ids+=session.scalars(select(r.RecordTemplateRevision.template_id).distinct()).all()
    values=[configured(session,identifier) for identifier in dict.fromkeys(ids)]
    return values if include_disabled else [t for t in values if t.get('enabled',True)]


class FieldConfig(r.Input):
    id:str=Field(pattern=r'^[a-z][a-z0-9_]{0,49}$')
    label:str=Field(min_length=1,max_length=2000)
    description:str=Field(default='',max_length=1000)
    question_aliases:list[str]=Field(default_factory=list,max_length=20)
    required:bool=False

    @field_validator('question_aliases')
    @classmethod
    def aliases(cls,values):
        if any(not v.strip() or len(v)>1000 for v in values):raise ValueError('问题别名须为1至1000字')
        return list(dict.fromkeys(r.plain_text(v.strip()) for v in values))


class TemplateUpdate(r.Input):
    expected_revision:int=Field(ge=0)
    name:str=Field(min_length=1,max_length=200)
    description:str=Field(default='',max_length=2000)
    scenario:r.Scenario='other'
    enabled:bool=True
    fields:list[FieldConfig]=Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def bounded_content(self):
        if sum(len(f.label)+len(f.description)+sum(map(len,f.question_aliases)) for f in self.fields)>30000:
            raise ValueError('模板字段、说明和问题别名总计不能超过30000字')
        return self


@router.get('')
def listing(request:Request):
    with Session(request.app.state.engine) as session:return {'items':available(session,True)}


@router.get('/{template_id}/versions')
def versions(template_id:str,request:Request):
    with Session(request.app.state.engine) as session:
        configured(session,template_id)
        return {'items':[{'revision':item.revision,'created_at':r.timestamp(item.created_at),'definition':item.definition} for item in session.scalars(select(r.RecordTemplateRevision).where(r.RecordTemplateRevision.template_id==template_id).order_by(r.RecordTemplateRevision.revision.desc()))]}


@router.put('/{template_id}')
def save(template_id:str,body:TemplateUpdate,request:Request):
    # Explicit check also protects this route when mounted separately from the app middleware.
    if (getattr(request.state,'user',None) or {}).get('role')!='admin':raise HTTPException(403,'仅管理员可配置笔录模板。')
    import re
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,19}',template_id):raise HTTPException(422,'模板标识格式无效。')
    with r.write_session(request.app.state.engine) as session:
        latest=current(session,template_id)
        if body.expected_revision!=(latest.revision if latest else 0):raise HTTPException(409,'模板配置已更新，请重新读取后保存。')
        base=next((t for t in r.ALL_TEMPLATES if t['id']==template_id),None)
        ids=[f.id for f in body.fields]
        if len(set(ids))!=len(ids):raise HTTPException(422,'模板字段标识不能重复。')
        if base:
            fields={f.id:f for f in body.fields}
            if set(fields)!={f['id'] for f in base['fields']} or any(fields[f['id']].label!=f['label'] for f in base['fields']):
                raise HTTPException(422,'原 Word 模板的字段和问题保持原样，请只配置提取说明、问题别名及必填项。')
            definition={**deepcopy(base),'fields':[{**f,**fields[f['id']].model_dump()} for f in base['fields']]}
        else:
            definition={'id':template_id,'example':False,'fields':[f.model_dump() for f in body.fields]}
        revision=body.expected_revision+1
        definition.update(name=body.name.strip(),description=body.description,scenario=body.scenario,enabled=body.enabled,config_revision=revision,version=f"{base['version'] if base else '1'}.c{revision}")
        session.add(r.RecordTemplateRevision(template_id=template_id,revision=revision,definition=definition,actor_id=request.state.user['id']))
        r.audit(session,'template_configured',template_id=template_id,template_revision=revision,actor_id=request.state.user['id'])
        session.flush()
        return definition
