"""Alert-owned editable decision drafts; independent of the retired document centre."""
import re
from datetime import datetime, timezone
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import ForeignKey, String, Integer, JSON, DateTime, select
from sqlalchemy.orm import Mapped, mapped_column
from fastapi import APIRouter, Request, HTTPException, Response
from pydantic import BaseModel, Field, ConfigDict
from .database import Base
from .departure_documents import PARAGRAPHS, ASSET, W, EXTRA_FIELDS
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from io import BytesIO

PATTERN = re.compile(r'\{\{\s*([a-z_]+)\s*\}\}')
LABELS = dict(EXTRA_FIELDS) | {'person_name':'姓名','nationality':'国籍','document_number':'证件号码','organization':'办案单位','document_number_label':'文书编号','facts':'违法事实（人工填写）','legal_basis':'法律依据（人工填写）','decision':'处理决定（人工填写）','deadline':'离境期限（人工填写）','officer':'承办人','document_date':'文书日期'}
from .decision_layout import LABELS as TEMPLATE_LABELS, defaults, layout
LABELS.update(TEMPLATE_LABELS)

class ExpiryDecision(Base):
    __tablename__='expiry_decisions'
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64),unique=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey('document_expiry_alerts.id'),index=True)
    fields: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer,default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
    __mapper_args__={'version_id_col':revision}

def ensure_draft(session,task,person,alert,now):
    identifier=str(uuid5(NAMESPACE_URL,'crj:expiry-decision:'+task.id))
    row=session.get(ExpiryDecision,identifier)
    if row is None:
        values={k:'' for k in LABELS}
        values.update(person_name=person.name,english_name=person.english_name,nationality=person.nationality,document_number=person.document_number,gender={'male':'男','female':'女'}.get(person.gender,''),birth_date=person.birth_date.isoformat() if person.birth_date else '',document_type={'passport':'护照','other':'其他证件'}.get(person.document_type,person.document_type),address=person.address)
        row=ExpiryDecision(id=identifier,task_id=task.id,alert_id=alert.id,fields=values,snapshot={'person_id':person.id,'expiry':person.document_expiry.isoformat(),'captured_at':now.isoformat()},updated_at=now)
        session.add(row);session.flush()
    task.status='draft_created'
    return row

def paragraphs():
    with ZipFile(ASSET) as z:
        root=ET.fromstring(z.read('word/document.xml'))
        return [PARAGRAPHS.get(i,''.join(t.text or '' for t in p.iter(W+'t'))) for i,p in enumerate(root.find(W+'body').findall(W+'p'))]

def payload(row):
    return {'id':row.id,'revision':row.revision,'fields':defaults(row.fields),'labels':LABELS,'layout':layout(),'paragraphs':paragraphs(),'snapshot':row.snapshot,'missing':[key for key in LABELS if not row.fields.get(key,'').strip()]}

router=APIRouter(prefix='/api/alerts')

def get_row(session, alert_id, request):
    if request.state.user['phone']=='root': raise HTTPException(403,'请使用业务账号处理预警文书。')
    from .alerts import ExpiryDocumentTask
    row=session.scalar(select(ExpiryDecision).where(ExpiryDecision.alert_id==alert_id).order_by(ExpiryDecision.updated_at.desc()))
    if row is None: raise HTTPException(404,'尚未达到逾期七天，或扫描尚未完成。')
    task=session.get(ExpiryDocumentTask,row.task_id)
    from .people import Person
    from .alerts import business_today
    person=session.get(Person,row.snapshot['person_id'])
    if not task or task.status=='cancelled' or not person or person.archived or person.document_expiry != task.expiry or (business_today()-task.expiry).days<7:
        raise HTTPException(409,'档案已更新或不再满足生成条件，请刷新预警列表。')
    return row

@router.get('/{alert_id}/decision')
def read_decision(alert_id:str,request:Request):
    from .alerts import locked_session
    with locked_session(request.app.state.engine) as session:
        return payload(get_row(session,alert_id,request))

class Update(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=1)
    fields:dict[str,str]

@router.put('/{alert_id}/decision')
def update_decision(alert_id:str,body:Update,request:Request):
    from .alerts import locked_session, audit
    if set(body.fields)-set(LABELS) or any(len(v)>6000 or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',v) for v in body.fields.values()):
        raise HTTPException(422,'文书字段包含无效名称、过长内容或控制字符。')
    with locked_session(request.app.state.engine) as session:
        row=get_row(session,alert_id,request)
        if row.revision!=body.expected_revision: raise HTTPException(409,'文书已被其他人修改，请重新打开后编辑。')
        row.fields={**row.fields,**body.fields};row.updated_at=datetime.now(timezone.utc)
        session.flush();audit(session,'alerts.decision_updated',alert_id=alert_id)
        return payload(row)

from .decision_layout import make_docx

@router.get('/{alert_id}/decision.docx')
def download_decision(alert_id:str,request:Request):
    from .alerts import locked_session,audit
    with locked_session(request.app.state.engine) as session:
        row=get_row(session,alert_id,request);data=make_docx(row.fields)
        audit(session,'alerts.decision_exported',alert_id=alert_id)
        return Response(data,media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':'attachment; filename="departure-decision.docx"','Cache-Control':'no-store'})
