"""Source-backed facts and explicitly recorded relationships. No inferred risk scores."""
from datetime import date
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, model_validator
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text, or_, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from .database import Base
from .people import InputModel, PeopleRoute, Person, PersonStay, add_audit, detail, find_person, timestamp, utc_now
from .records import RecordSession, RecordSegment
from .documents import DocumentDraft
from .alerts import Alert


class ProfileFact(Base):
    __tablename__ = 'profile_facts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    person_id: Mapped[str] = mapped_column(ForeignKey('people.id'), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    label: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source: Mapped[str] = mapped_column(String(1000))
    source_snapshot: Mapped[dict] = mapped_column(JSON)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    review_note: Mapped[str] = mapped_column(String(1000), default='')
    occurred_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProfileRelation(Base):
    __tablename__ = 'profile_relations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    person_id: Mapped[str] = mapped_column(ForeignKey('people.id'), index=True)
    target_id: Mapped[str] = mapped_column(ForeignKey('people.id'), index=True)
    label: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(1000))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    review_note: Mapped[str] = mapped_column(String(1000), default='')
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utc_now)


class Evidence(InputModel):
    label: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=1000)
    verified: bool = False
    review_note: str = Field(default='', max_length=1000)

    @model_validator(mode='after')
    def review(self):
        if self.verified and not self.review_note:
            raise ValueError('核验事实须填写核验说明')
        if any('\x00' in value for value in self.model_dump().values() if isinstance(value, str)):
            raise ValueError('文本不能包含空字符')
        return self


class FactInput(Evidence):
    kind: Literal['identity', 'purpose', 'residence', 'other'] = 'other'
    value: str = Field(min_length=1, max_length=4000)
    source_type: Literal['manual', 'stay', 'record', 'document'] = 'manual'
    source_id: str | None = Field(default=None, max_length=36)
    occurred_on: date | None = None


class FactUpdate(FactInput):
    expected_revision: int = Field(ge=1)
    archived: bool = False


class RelationInput(Evidence):
    target_id: str = Field(min_length=1, max_length=36)


class RelationUpdate(RelationInput):
    expected_revision: int = Field(ge=1)
    archived: bool = False


router = APIRouter(prefix='/api/profiles', tags=['事实画像'], route_class=PeopleRoute)


def serialize(row):
    return {column.name: timestamp(value) if column.name.endswith('_at') else value
            for column in row.__table__.columns if (value := getattr(row, column.name)) is not None}


def source_snapshot(session, person_id, data):
    if data.source_type == 'manual':
        if data.source_id:
            raise HTTPException(422, '人工资料不应包含业务记录标识。')
        return {'source': data.source, 'captured_at': timestamp(utc_now())}
    model = {'stay': PersonStay, 'record': RecordSession, 'document': DocumentDraft}[data.source_type]
    row = session.get(model, data.source_id) if data.source_id else None
    if not row or row.person_id != person_id:
        raise HTTPException(422, '来源记录不存在或不属于当前人员。')
    snapshot = {'id': row.id, 'updated_at': timestamp(row.updated_at), 'source_type': data.source_type}
    if isinstance(row, PersonStay):
        snapshot.update(source=row.source, entry_date=row.entry_date.isoformat(), permitted_until=str(row.permitted_until) if row.permitted_until else None, exit_date=str(row.exit_date) if row.exit_date else None)
    else:
        snapshot.update(title=row.title, revision=row.revision, status=row.status)
    return snapshot


def editable(session, person_id):
    person = find_person(session, person_id)
    if person.archived:
        raise HTTPException(409, '已归档人员只读，请先在人员档案中恢复。')
    return person


@router.get('/{person_id}')
def profile(person_id: str, request: Request):
    with Session(request.app.state.engine) as session:
        person = find_person(session, person_id)
        from .migration import MigrationItem
        origins = [{'source_key': row.source_key, 'legacy_id': row.legacy_id, 'original': row.original, 'created_at': timestamp(row.created_at)} for row in session.scalars(select(MigrationItem).where(MigrationItem.person_id == person_id))]
        result = {'origins': origins, 'person': detail(session, person), 'facts': [], 'relations': [], 'timeline': [], 'sources': []}
        result['facts'] = [serialize(row) for row in session.scalars(select(ProfileFact).where(ProfileFact.person_id == person_id).order_by(ProfileFact.updated_at.desc()))]
        for row in session.scalars(select(ProfileRelation).where(or_(ProfileRelation.person_id == person_id, ProfileRelation.target_id == person_id)).order_by(ProfileRelation.created_at.desc())):
            other = find_person(session, row.target_id if row.person_id == person_id else row.person_id)
            result['relations'].append({**serialize(row), 'other': {'id': other.id, 'name': other.name, 'code': other.code, 'archived': other.archived}})
        timeline = [{'id': person.id, 'kind': 'person', 'date': timestamp(person.created_at), 'title': '建立人员档案', 'detail': person.source, 'status': person.verification}]
        for stay in result['person']['stays']:
            result['sources'].append({'id': stay['id'], 'type': 'stay', 'title': f"停留记录 · {stay['entry_date']}", 'detail': stay['source']})
            timeline.append({'id': stay['id'], 'kind': 'stay', 'date': str(stay['entry_date']), 'title': '登记入境事实', 'detail': stay['source'], 'status': '已登记'})
            if stay['exit_date']:
                timeline.append({'id': stay['id']+'-exit', 'kind': 'stay', 'date': str(stay['exit_date']), 'title': '登记离境事实', 'detail': stay['source'], 'status': '已登记'})
        for model, kind, title in [(RecordSession, 'record', '询问笔录'), (DocumentDraft, 'document', '业务文书')]:
            for row in session.scalars(select(model).where(model.person_id == person_id).order_by(model.created_at.desc())):
                # Archived sources remain traceable; no AI summaries become verified facts implicitly.
                source = {'id': row.id, 'type': kind, 'title': row.title, 'detail': f'版本 {row.revision} · {row.status}', 'revision': row.revision}
                if kind == 'record':
                    segments = session.scalars(select(RecordSegment).where(RecordSegment.record_id == row.id).order_by(RecordSegment.position)).all()
                    source['excerpt'] = '\n'.join(s.text for s in segments)[:12000]
                else:
                    source['excerpt'] = str(row.review_note or '文书快照保留在文书中心')
                result['sources'].append(source)
                timeline.append({'id': row.id, 'kind': kind, 'date': timestamp(row.created_at), 'title': title+' · '+row.title, 'detail': f'版本 {row.revision}', 'status': row.status})
        for alert in session.scalars(select(Alert).where(Alert.person_id == person_id)):
            timeline.append({'id': alert.id, 'kind': 'alert', 'date': timestamp(alert.updated_at), 'title': '期限核查记录', 'detail': alert.reason, 'status': alert.status})
        for fact in result['facts']:
            timeline.append({'id': fact['id'], 'kind': 'fact', 'date': str(fact.get('occurred_on') or fact['created_at']), 'title': fact['label'], 'detail': fact['source'], 'status': '已归档' if fact['archived'] else '已核验' if fact['verified'] else '待核验'})
        result['timeline'] = sorted(timeline, key=lambda item: item['date'][:10]+item['date'][10:], reverse=True)
        return result


@router.post('/{person_id}/facts', status_code=201)
def create_fact(person_id: str, data: FactInput, request: Request):
    with Session(request.app.state.engine) as session:
        editable(session, person_id)
        row = ProfileFact(person_id=person_id, **data.model_dump(), source_snapshot=source_snapshot(session, person_id, data))
        session.add(row); session.flush()
        add_audit(session, 'profile.fact_created', person_id=person_id, fact_id=row.id)
        session.commit(); return serialize(row)


@router.put('/{person_id}/facts/{fact_id}')
def update_fact(person_id: str, fact_id: str, data: FactUpdate, request: Request):
    with Session(request.app.state.engine) as session:
        editable(session, person_id)
        values = data.model_dump(exclude={'expected_revision'})
        values.update(source_snapshot=source_snapshot(session, person_id, data), revision=data.expected_revision+1, updated_at=utc_now())
        result = session.execute(update(ProfileFact).where(ProfileFact.id == fact_id, ProfileFact.person_id == person_id, ProfileFact.revision == data.expected_revision).values(**values))
        if result.rowcount != 1:
            raise HTTPException(409, '事实已被更新或不存在，请刷新后重试。')
        add_audit(session, 'profile.fact_updated', person_id=person_id, fact_id=fact_id, revision=data.expected_revision+1)
        session.commit(); return serialize(session.get(ProfileFact, fact_id))


def validate_relation(session, person_id, data):
    editable(session, person_id)
    if getattr(data, 'archived', False): find_person(session, data.target_id)
    else: editable(session, data.target_id)
    if person_id == data.target_id:
        raise HTTPException(422, '不能建立指向本人的关系。')


@router.post('/{person_id}/relations', status_code=201)
def create_relation(person_id: str, data: RelationInput, request: Request):
    with Session(request.app.state.engine) as session:
        validate_relation(session, person_id, data)
        row = ProfileRelation(person_id=person_id, **data.model_dump())
        session.add(row); session.flush()
        add_audit(session, 'profile.relation_created', person_id=person_id, relation_id=row.id)
        session.commit(); return serialize(row)


@router.put('/{person_id}/relations/{relation_id}')
def update_relation(person_id: str, relation_id: str, data: RelationUpdate, request: Request):
    with Session(request.app.state.engine) as session:
        validate_relation(session, person_id, data)
        result = session.execute(update(ProfileRelation).where(ProfileRelation.id == relation_id, ProfileRelation.person_id == person_id, ProfileRelation.revision == data.expected_revision).values(**data.model_dump(exclude={'expected_revision'}), revision=data.expected_revision+1, updated_at=utc_now()))
        if result.rowcount != 1:
            raise HTTPException(409, '关系已被更新或不存在，请从登记人员画像刷新后修改。')
        add_audit(session, 'profile.relation_updated', person_id=person_id, relation_id=relation_id, revision=data.expected_revision+1)
        session.commit(); return serialize(session.get(ProfileRelation, relation_id))
