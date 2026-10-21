"""Human-reviewed migration batches. Preview and commit repeat identical validation."""
import hashlib
import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, ValidationError
from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .database import Base
from .people import InputModel, PeopleRoute, Person, PersonInput, PersonStay, StayInput, add_audit, new_person, utc_now, validation_message


class MigrationItem(Base):
    __tablename__ = 'migration_items'
    __table_args__ = (UniqueConstraint('source_key', 'legacy_id', name='uq_migration_origin'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_key: Mapped[str] = mapped_column(String(100))
    legacy_id: Mapped[str] = mapped_column(String(100))
    person_id: Mapped[str] = mapped_column(ForeignKey('people.id'))
    fingerprint: Mapped[str] = mapped_column(String(64))
    original: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utc_now)


class Batch(InputModel):
    source_key: str = Field(min_length=1, max_length=100)
    items: list[dict] = Field(min_length=1, max_length=500)


class Commit(Batch):
    preview_hash: str = Field(min_length=64, max_length=64)
    confirmed: bool


router = APIRouter(prefix='/api/migration', tags=['本地迁移'], route_class=PeopleRoute)


def prepare(session, data):
    raw = data.model_dump(include={'source_key', 'items'})
    encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    if len(encoded) > 2 * 1024 * 1024: raise HTTPException(413, '迁移批次不得超过 2 MiB。')
    digest = hashlib.sha256(encoded).hexdigest()
    valid, errors, skipped, seen_ids, seen_docs = [], [], 0, set(), set()
    for index, item in enumerate(data.items, 1):
        try:
            if set(item) - {'legacy_id', 'person', 'stays', 'original'}: raise ValueError('条目包含不支持的字段')
            legacy_id = item.get('legacy_id')
            if not isinstance(legacy_id, str) or not legacy_id.strip() or len(legacy_id) > 100: raise ValueError('请填写原始记录 legacy_id（最长 100 字符）')
            if legacy_id in seen_ids: raise ValueError('批次中原始记录标识重复')
            seen_ids.add(legacy_id)
            if not isinstance(item.get('original', {}), dict): raise ValueError('original 必须是原始资料对象')
            fingerprint = hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            prior = session.scalar(select(MigrationItem).where(MigrationItem.source_key == data.source_key, MigrationItem.legacy_id == legacy_id))
            if prior:
                if prior.fingerprint != fingerprint: raise ValueError('该原始记录已导入且内容有变化，请在现有档案中核对；不覆盖')
                skipped += 1; continue
            person = PersonInput.model_validate(item.get('person', {}))
            if person.verification != 'pending': raise ValueError('迁入档案须为 pending，迁入后再人工核验')
            stays_raw = item.get('stays', [])
            if not isinstance(stays_raw, list) or len(stays_raw) > 100: raise ValueError('每人最多 100 条停留事实')
            stays = [StayInput.model_validate(row) for row in stays_raw]
            key = (person.document_type, person.document_number)
            if key in seen_docs: raise ValueError('批次中存在重复证件，请先合并核对')
            seen_docs.add(key)
            if session.scalar(select(Person.id).where(Person.document_type == key[0], Person.document_number == key[1])): raise ValueError('相同证件已有档案，不自动合并或覆盖')
            valid.append((legacy_id, person, stays, item.get('original', {}), fingerprint))
        except ValidationError as error:
            errors.append({'row': index, 'message': validation_message(error.errors())})
        except ValueError as error:
            errors.append({'row': index, 'message': str(error)})
        except TypeError:
            errors.append({'row': index, 'message': '条目结构不正确'})
    return {'total': len(data.items), 'ready': len(valid), 'skipped': skipped, 'errors': errors, 'preview_hash': digest, 'can_import': not errors and bool(valid)}, valid


@router.post('/preview')
def preview(data: Batch, request: Request):
    with Session(request.app.state.engine) as session: return prepare(session, data)[0]


@router.post('/commit')
def commit(data: Commit, request: Request):
    if not data.confirmed: raise HTTPException(422, '请确认已人工核对本批次及日期含义。')
    with Session(request.app.state.engine) as session:
        result, rows = prepare(session, data)
        if result['preview_hash'] != data.preview_hash: raise HTTPException(409, '预检后资料有变化，请重新预检。')
        if result['errors']: raise HTTPException(409, '批次未通过校验，未导入任何记录；请重新预检。')
        for legacy_id, person_input, stays, original, fingerprint in rows:
            person = new_person(person_input); session.add(person); session.flush()
            for stay in stays: session.add(PersonStay(person_id=person.id, **stay.model_dump()))
            session.add(MigrationItem(source_key=data.source_key, legacy_id=legacy_id, person_id=person.id, original=original, fingerprint=fingerprint))
        add_audit(session, 'migration.imported', imported=len(rows), skipped=result['skipped'])
        session.commit(); return {'imported': len(rows), 'skipped': result['skipped']}
