"""Account-scoped conversation history, persisted by the server before SSE completion."""
import json
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import String, Text, Integer, select, func, update
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base
from .knowledge import _session, _WRITE_LOCK

class AssistantConversation(Base):
    __tablename__ = 'assistant_conversations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(100))
    updated_at: Mapped[str] = mapped_column(String(40), index=True)

class AssistantMessage(Base):
    __tablename__ = 'assistant_messages'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    data: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default='pending')

router = APIRouter(prefix='/api/knowledge/conversations', tags=['助手会话'])
def now(): return datetime.now(timezone.utc).isoformat()
def owned(session, request, conversation_id):
    row = session.get(AssistantConversation, conversation_id)
    if not row or row.owner_id != request.state.user['id']:
        raise HTTPException(404, '会话不存在')
    return row

@router.get('')
def list_conversations(request: Request, offset: int = 0):
    if offset < 0: raise HTTPException(422, '页码无效')
    with _session(request) as session:
        rows = session.scalars(select(AssistantConversation).where(AssistantConversation.owner_id == request.state.user['id']).order_by(AssistantConversation.updated_at.desc(), AssistantConversation.id).offset(offset).limit(51)).all()
        return {'items': [{'id':r.id,'title':r.title,'updated_at':r.updated_at} for r in rows[:50]], 'has_more':len(rows)>50}

@router.get('/{conversation_id}')
def get_conversation(request: Request, conversation_id: str):
    with _session(request) as session:
        row = owned(session, request, conversation_id)
        messages = session.scalars(select(AssistantMessage).where(AssistantMessage.conversation_id == row.id).order_by(AssistantMessage.ordinal)).all()
        return {'id':row.id,'title':row.title,'items':[{**json.loads(m.data),'id':m.id,'status':m.status,'streaming':False, **({'error':'上次生成尚未完成，可稍后重新打开会话，或重试。'} if m.status=='pending' else {})} for m in messages]}

def begin_turn(request, body, template_name):
    with _WRITE_LOCK, _session(request, write=True) as session:
        if body.conversation_id:
            row = owned(session,request,body.conversation_id)
        else:
            row = AssistantConversation(id=str(uuid4()),owner_id=request.state.user['id'],title=body.question[:80],updated_at=now())
            session.add(row)
        count = session.scalar(select(func.count()).select_from(AssistantMessage).where(AssistantMessage.conversation_id==row.id))
        if count >= 200: raise HTTPException(409, '当前会话已满，请新建会话继续。')
        data = {'question':body.question,'date':str(body.as_of),'templateId':body.template_id,'templateRevision':body.template_revision,'templateName':template_name}
        message = AssistantMessage(id=str(uuid4()),conversation_id=row.id,ordinal=count+1,data=json.dumps(data,ensure_ascii=False),status='pending')
        row.updated_at = now()
        session.add(message)
        return row.id,message.id

def finish_turn(request, conversation_id, message_id, *, answer=None, error=None):
    with _WRITE_LOCK, _session(request, write=True) as session:
        row=owned(session,request,conversation_id)
        message=session.get(AssistantMessage,message_id)
        if not message or message.conversation_id != row.id: raise HTTPException(404,'消息不存在')
        data=json.loads(message.data)
        if answer is not None: data['answer']=answer
        if error: data['error']=error
        message.data=json.dumps(data,ensure_ascii=False)
        message.status='complete' if answer is not None else 'error'
        row.updated_at=now()

def recover_history(engine):
    from sqlalchemy.orm import Session
    with Session(engine) as session, session.begin():
        for message in session.scalars(select(AssistantMessage).where(AssistantMessage.status=='pending')):
            data=json.loads(message.data);data['error']='服务已重启，上次生成未完成，请重试。'
            message.data=json.dumps(data,ensure_ascii=False);message.status='error'
