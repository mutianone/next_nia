"""Local factual person records, stay records and transactional CSV exchange."""

import csv
from datetime import date, datetime, timezone
from io import StringIO
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, case, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column

from .database import AuditEvent, Base

MAX_CSV_BYTES = 1024 * 1024
MAX_CSV_ROWS = 500


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def business_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


class Person(Base):
    __tablename__ = "people"
    __table_args__ = (UniqueConstraint("document_type", "document_number", name="uq_people_document"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    english_name: Mapped[str] = mapped_column(String(200), default="")
    nationality: Mapped[str] = mapped_column(String(100), index=True)
    gender: Mapped[str] = mapped_column(String(12), default="unknown")
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    document_type: Mapped[str] = mapped_column(String(20), default="passport")
    document_number: Mapped[str] = mapped_column(String(100), index=True)
    document_expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    phone: Mapped[str] = mapped_column(String(80), default="")
    address: Mapped[str] = mapped_column(String(1000), default="")
    purpose: Mapped[str] = mapped_column(String(500), default="")
    source: Mapped[str] = mapped_column(String(500))
    verification: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class PersonStay(Base):
    __tablename__ = "person_stays"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), index=True)
    entry_date: Mapped[date] = mapped_column(Date)
    permitted_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    permit_type: Mapped[str] = mapped_column(String(100), default="")
    permit_number: Mapped[str] = mapped_column(String(100), default="")
    purpose: Mapped[str] = mapped_column(String(500), default="")
    source: Mapped[str] = mapped_column(String(500))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PersonInput(InputModel):
    name: str = Field(min_length=1, max_length=100)
    english_name: str = Field(default="", max_length=200)
    nationality: str = Field(min_length=1, max_length=100)
    gender: Literal["unknown", "male", "female"] = "unknown"
    birth_date: date | None = None
    document_type: Literal["passport", "other"] = "passport"
    document_number: str = Field(min_length=1, max_length=100)
    document_expiry: date | None = None
    phone: str = Field(default="", max_length=80)
    address: str = Field(default="", max_length=1000)
    purpose: str = Field(default="", max_length=500)
    source: str = Field(min_length=1, max_length=500)
    verification: Literal["pending", "verified"] = "pending"
    notes: str = Field(default="", max_length=5000)

    @field_validator("birth_date", "document_expiry", mode="before")
    @classmethod
    def blank_date(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("document_number", mode="before")
    @classmethod
    def normalize_document(cls, value):
        return "".join(value.split()).upper() if isinstance(value, str) else value

    @field_validator("name", "english_name", "nationality", "document_number", "phone", "address", "purpose", "source", "notes")
    @classmethod
    def reject_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("文本不能包含空字符")
        return value

    @model_validator(mode="after")
    def valid_dates(self):
        if self.birth_date and self.birth_date > business_today():
            raise ValueError("出生日期不能晚于今天")
        if self.birth_date and self.document_expiry and self.document_expiry < self.birth_date:
            raise ValueError("证件有效期不能早于出生日期")
        return self


class StayInput(InputModel):
    entry_date: date
    permitted_until: date | None = None
    exit_date: date | None = None
    permit_type: str = Field(default="", max_length=100)
    permit_number: str = Field(default="", max_length=100)
    purpose: str = Field(default="", max_length=500)
    source: str = Field(min_length=1, max_length=500)
    notes: str = Field(default="", max_length=5000)

    @field_validator("permitted_until", "exit_date", mode="before")
    @classmethod
    def blank_date(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("permit_type", "permit_number", "purpose", "source", "notes")
    @classmethod
    def reject_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("文本不能包含空字符")
        return value

    @model_validator(mode="after")
    def valid_dates(self):
        if self.permitted_until and self.permitted_until < self.entry_date:
            raise ValueError("允许停留截止日期不能早于入境日期")
        if self.exit_date and self.exit_date < self.entry_date:
            raise ValueError("离境日期不能早于入境日期")
        # Departure after a recorded permitted date is a fact, not an automatic
        # legal conclusion. Unknown permitted dates remain null.
        return self


class ArchiveInput(InputModel):
    archived: bool


class CSVInput(InputModel):
    model_config = ConfigDict(str_strip_whitespace=False)
    csv_text: str = Field(max_length=MAX_CSV_BYTES)


FIELD_LABELS = {
    "name": "姓名", "english_name": "英文姓名", "nationality": "国籍", "gender": "性别",
    "birth_date": "出生日期", "document_type": "证件类型", "document_number": "证件号码",
    "document_expiry": "证件有效期", "phone": "联系电话", "address": "地址", "purpose": "事由",
    "source": "资料来源", "verification": "核验状态", "notes": "备注", "archived": "归档状态",
    "entry_date": "入境日期", "permitted_until": "允许停留截止日期", "exit_date": "离境日期",
    "permit_type": "许可类型", "permit_number": "许可号码", "q": "搜索词", "page": "页码",
    "page_size": "每页条数", "csv_text": "CSV 内容",
}
DATE_FIELDS = {"birth_date", "document_expiry", "entry_date", "permitted_until", "exit_date"}
CUSTOM_ERRORS = {
    "出生日期不能晚于今天", "证件有效期不能早于出生日期", "允许停留截止日期不能早于入境日期",
    "离境日期不能早于入境日期", "文本不能包含空字符",
}


def validation_message(errors: list[dict]) -> str:
    messages = []
    for error in errors[:5]:
        location = error.get("loc", ())
        field = next((part for part in reversed(location) if part in FIELD_LABELS), None)
        label = FIELD_LABELS.get(field, "请求")
        custom = str(error.get("ctx", {}).get("error", ""))
        if custom in CUSTOM_ERRORS:
            message = custom
        elif error.get("type") == "extra_forbidden":
            message = "请求包含不支持或只读的字段"
        elif error.get("type") == "missing":
            message = f"请填写{label}"
        elif field in DATE_FIELDS:
            message = f"{label}格式不正确，请使用 YYYY-MM-DD"
        elif error.get("type") == "string_too_short":
            message = f"{label}不能为空"
        elif error.get("type") == "string_too_long":
            message = f"{label}超过允许长度"
        else:
            message = f"{label}格式或取值不正确"
        if message not in messages:
            messages.append(message)
    return "；".join(messages) or "资料格式不正确，请检查后重试。"


class PeopleRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as error:
                raise HTTPException(422, validation_message(error.errors())) from None
            except IntegrityError:
                raise HTTPException(409, "相同证件的档案已存在或记录发生冲突，请刷新后检查。") from None
            except SQLAlchemyError:
                raise HTTPException(503, "人员资料暂时无法读取或保存，请检查数据库状态后重试；未完成的修改已撤销。") from None

        return safe_handler


router = APIRouter(prefix="/api/people", tags=["人员事实档案"], route_class=PeopleRoute)
CSV_COLUMNS = list(PersonInput.model_fields)
CSV_REQUIRED = {"name", "nationality", "document_number", "source"}
CSV_FORMAT_COLUMN = "__nia_csv_format"
CSV_FORMAT_VERSION = "safe-v1"


def person_values(person: Person) -> dict:
    values = {name: getattr(person, name) for name in CSV_COLUMNS}
    values.update({"id": person.id, "code": person.code, "archived": person.archived,
                   "created_at": timestamp(person.created_at), "updated_at": timestamp(person.updated_at)})
    return values


def timestamp(value: datetime) -> str:
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).isoformat()


def stay_values(stay: PersonStay) -> dict:
    return {"id": stay.id, "person_id": stay.person_id,
            **{name: getattr(stay, name) for name in StayInput.model_fields},
            "created_at": timestamp(stay.created_at), "updated_at": timestamp(stay.updated_at)}


def detail(session: Session, person: Person) -> dict:
    stays = session.scalars(select(PersonStay).where(PersonStay.person_id == person.id).order_by(PersonStay.entry_date.desc(), PersonStay.id)).all()
    return {**person_values(person), "stays": [stay_values(stay) for stay in stays]}


def find_person(session: Session, person_id: str) -> Person:
    person = session.get(Person, person_id)
    if person is None:
        raise HTTPException(404, "未找到该人员档案。")
    return person


def add_audit(session: Session, action: str, **identifiers_or_counts) -> None:
    session.add(AuditEvent(action=action, details=identifiers_or_counts))


def new_person(data: PersonInput) -> Person:
    identifier = uuid4()
    return Person(id=str(identifier), code=f"NIA-{business_today():%Y%m%d}-{identifier.hex[:10].upper()}", **data.model_dump())


def check_duplicate(session: Session, data: PersonInput, exclude_id: str | None = None):
    query = select(Person.id).where(Person.document_type == data.document_type, Person.document_number == data.document_number)
    if exclude_id:
        query = query.where(Person.id != exclude_id)
    if session.scalar(query):
        raise HTTPException(409, "相同类型与号码的证件档案已存在，请检查现有或已归档的人员资料。")


def filters(q: str, archived: bool):
    conditions = [Person.archived == archived]
    if q.strip():
        search = q.strip()
        document_search = "".join(search.split()).upper()
        conditions.append(or_(Person.code.icontains(search, autoescape=True), Person.name.icontains(search, autoescape=True),
                              Person.english_name.icontains(search, autoescape=True), Person.nationality.icontains(search, autoescape=True),
                              Person.document_number.icontains(document_search, autoescape=True)))
    return conditions


def csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    value = str(value)
    # Spreadsheet programs may ignore leading whitespace before evaluating a
    # formula. Prefix the entire cell before CSV quoting in that case too.
    if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or value.startswith(("'", "\t", "\r", "\n")):
        return "'" + value
    return value


def csv_response(rows: list[dict], filename: str, *, reversible: bool = False) -> Response:
    stream = StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS + ([CSV_FORMAT_COLUMN] if reversible else []))
    for row in rows:
        cells = [csv_cell(row.get(column)) for column in CSV_COLUMNS]
        writer.writerow(cells + ([CSV_FORMAT_VERSION] if reversible else []))
    return Response(content=b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


def parse_csv(csv_text: str, session: Session) -> tuple[list[dict], list[dict], int, bool]:
    try:
        size = len(csv_text.encode("utf-8"))
    except UnicodeEncodeError:
        raise HTTPException(422, "CSV 包含无法识别的文本编码，请重新导出 UTF-8 CSV 文件。") from None
    if size > MAX_CSV_BYTES:
        raise HTTPException(413, "CSV 文件不能超过 1 MiB。")
    if "\x00" in csv_text:
        raise HTTPException(422, "CSV 内容不能包含空字符，请使用 UTF-8 编码的文本文件。")
    reader = csv.reader(StringIO(csv_text.lstrip("\ufeff"), newline=""), strict=True)
    errors = []
    parsed = []
    total = 0
    duplicate = False
    try:
        header = next(reader, None)
        if not header:
            return [], [{"row": 1, "message": "CSV 为空，请使用导入模板。"}], 0, False
        header = [name.strip() for name in header]
        if len(set(header)) != len(header) or not CSV_REQUIRED.issubset(header) or any(name not in CSV_COLUMNS and name != CSV_FORMAT_COLUMN for name in header):
            return [], [{"row": 1, "message": "CSV 表头无效：姓名 name、国籍 nationality、证件号码 document_number 和来源 source 为必需列；请使用下载的模板。"}], 0, False
        while True:
            row_number = reader.line_num + 1
            row = next(reader, None)
            if row is None:
                break
            if not any(cell.strip() for cell in row):
                continue
            total += 1
            if total > MAX_CSV_ROWS:
                raise HTTPException(413, "一次最多导入 500 条人员资料，请拆分文件。")
            if len(row) != len(header):
                errors.append({"row": row_number, "message": "此行列数与表头不一致，请检查逗号与引号。"})
                continue
            raw = dict(zip(header, row))
            if CSV_FORMAT_COLUMN in raw:
                if raw.pop(CSV_FORMAT_COLUMN) != CSV_FORMAT_VERSION:
                    errors.append({"row": row_number, "message": "CSV 安全格式标记无效，必须为 safe-v1；请重新下载导出文件或使用导入模板。"})
                    continue
                decoded = {column: value[1:] if value.startswith("'") else value for column, value in raw.items()}
                # Decode only a canonical encoding produced by this exporter.
                # Ordinary CSV has no marker and its apostrophes stay literal.
                if any(csv_cell(decoded[column]) != value for column, value in raw.items()):
                    errors.append({"row": row_number, "message": "CSV 安全编码不完整，可能已被编辑或被电子表格改写；请重新导出，或用导入模板填写原始文本。"})
                    continue
                raw = decoded
            for enum in ("gender", "document_type", "verification"):
                if raw.get(enum) == "":
                    raw.pop(enum)
            try:
                parsed.append((row_number, PersonInput.model_validate(raw)))
            except ValidationError as error:
                errors.append({"row": row_number, "message": validation_message(error.errors())})
    except csv.Error:
        return [], [{"row": max(reader.line_num, 1), "message": "CSV 引号或换行格式不正确，请重新导出 UTF-8 CSV 文件。"}], total, False
    existing = set()
    if parsed:
        numbers = {data.document_number for _, data in parsed}
        existing = set(session.execute(select(Person.document_type, Person.document_number).where(Person.document_number.in_(numbers))).all())
    seen = {}
    valid = []
    for row_number, data in parsed:
        identity = (data.document_type, data.document_number)
        if identity in existing:
            errors.append({"row": row_number, "message": "相同证件的档案已存在（包括已归档资料），请使用编辑功能。"})
            duplicate = True
        elif identity in seen:
            errors.append({"row": row_number, "message": f"证件与第 {seen[identity]} 行重复。"})
            duplicate = True
        else:
            valid.append(data.model_dump(mode="json"))
        seen.setdefault(identity, row_number)
    if total == 0:
        errors.append({"row": 2, "message": "CSV 没有人员数据行。"})
    return valid, sorted(errors, key=lambda item: item["row"]), total, duplicate


@router.get("")
def list_people(request: Request, q: str = Query(default="", max_length=200), archived: bool = False,
                page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                nationality: str = Query(default="", max_length=100),
                gender: Literal["all", "male", "female", "unknown"] = "all",
                verification: Literal["all", "verified", "pending"] = "all"):
    with Session(request.app.state.engine) as session:
        conditions = filters(q, archived)
        if nationality.strip():
            conditions.append(Person.nationality.icontains(nationality.strip(), autoescape=True))
        if gender != "all":
            conditions.append(Person.gender == gender)
        if verification != "all":
            conditions.append(Person.verification == verification)
        total = session.scalar(select(func.count()).select_from(Person).where(*conditions)) or 0
        people = session.scalars(select(Person).where(*conditions).order_by(Person.updated_at.desc(), Person.id).offset((page - 1) * page_size).limit(page_size)).all()
        counts = session.execute(select(func.count(Person.id),
            func.coalesce(func.sum(case(((Person.archived == False) & (Person.verification == "verified"), 1), else_=0)), 0),
            func.coalesce(func.sum(case(((Person.archived == False) & (Person.verification == "pending"), 1), else_=0)), 0),
            func.coalesce(func.sum(case((Person.archived == True, 1), else_=0)), 0))).one()
        return {"items": [person_values(person) for person in people], "total": total, "page": page, "page_size": page_size,
                "stats": {"total": counts[0], "verified": counts[1], "pending": counts[2], "archived": counts[3]}}


@router.get("/template.csv")
def download_template():
    example = PersonInput(name="示例人员（请替换）", english_name="Example Person", nationality="示例国籍", document_number="DEMO-REPLACE-001", source="示例数据（请填写实际来源）")
    return csv_response([example.model_dump()], "people-template.csv")


@router.get("/export.csv")
def export_people(request: Request, q: str = Query(default="", max_length=200), archived: bool = False):
    with Session(request.app.state.engine) as session:
        people = session.scalars(select(Person).where(*filters(q, archived)).order_by(Person.code)).all()
        return csv_response([person_values(person) for person in people], "people-export.csv", reversible=True)


@router.post("/import/preview")
def preview_import(data: CSVInput, request: Request):
    with Session(request.app.state.engine) as session:
        rows, errors, total, _ = parse_csv(data.csv_text, session)
        return {"rows": rows, "errors": errors, "total": total, "valid_count": len(rows)}


@router.post("/import")
def import_people(data: CSVInput, request: Request):
    with Session(request.app.state.engine) as session, session.begin():
        rows, errors, _, duplicate = parse_csv(data.csv_text, session)
        if errors:
            explanation = "；".join(f"第 {item['row']} 行：{item['message']}" for item in errors[:5])
            raise HTTPException(409 if duplicate else 422, f"导入未写入任何资料。{explanation}")
        for row in rows:
            session.add(new_person(PersonInput.model_validate(row)))
        add_audit(session, "people.imported", count=len(rows))
        session.flush()
        return {"imported": len(rows)}


@router.post("", status_code=201)
def create_person(data: PersonInput, request: Request):
    with Session(request.app.state.engine) as session, session.begin():
        check_duplicate(session, data)
        person = new_person(data)
        session.add(person)
        session.flush()
        add_audit(session, "person.created", person_id=person.id)
        return detail(session, person)


@router.get("/{person_id}")
def get_person(person_id: str, request: Request):
    with Session(request.app.state.engine) as session:
        return detail(session, find_person(session, person_id))


@router.put("/{person_id}")
def update_person(person_id: str, data: PersonInput, request: Request):
    with Session(request.app.state.engine) as session, session.begin():
        person = find_person(session, person_id)
        check_duplicate(session, data, person_id)
        if data.birth_date and session.scalar(select(PersonStay.id).where(PersonStay.person_id == person_id, PersonStay.entry_date < data.birth_date).limit(1)):
            raise HTTPException(422, "出生日期不能晚于已有停留记录的入境日期，请先核对资料。")
        for name, value in data.model_dump().items():
            setattr(person, name, value)
        person.updated_at = utc_now()
        add_audit(session, "person.updated", person_id=person.id)
        session.flush()
        return detail(session, person)


@router.post("/{person_id}/archive")
def archive_person(person_id: str, data: ArchiveInput, request: Request):
    with Session(request.app.state.engine) as session, session.begin():
        person = find_person(session, person_id)
        person.archived = data.archived
        person.updated_at = utc_now()
        add_audit(session, "person.archived" if data.archived else "person.restored", person_id=person.id)
        session.flush()
        return detail(session, person)


def validate_stay_person(person: Person, data: StayInput):
    if person.birth_date and data.entry_date < person.birth_date:
        raise HTTPException(422, "入境日期不能早于该人员出生日期，请核对资料。")


@router.post("/{person_id}/stays", status_code=201)
def create_stay(person_id: str, data: StayInput, request: Request):
    with Session(request.app.state.engine) as session, session.begin():
        person = find_person(session, person_id)
        validate_stay_person(person, data)
        stay = PersonStay(person_id=person_id, **data.model_dump())
        session.add(stay)
        person.updated_at = utc_now()
        session.flush()
        add_audit(session, "person.stay_created", person_id=person_id, stay_id=stay.id)
        return stay_values(stay)


@router.put("/{person_id}/stays/{stay_id}")
def update_stay(person_id: str, stay_id: str, data: StayInput, request: Request):
    with Session(request.app.state.engine) as session, session.begin():
        person = find_person(session, person_id)
        validate_stay_person(person, data)
        stay = session.scalar(select(PersonStay).where(PersonStay.id == stay_id, PersonStay.person_id == person_id))
        if stay is None:
            raise HTTPException(404, "未找到该人员的停留记录。")
        for name, value in data.model_dump().items():
            setattr(stay, name, value)
        stay.updated_at = person.updated_at = utc_now()
        add_audit(session, "person.stay_updated", person_id=person_id, stay_id=stay_id)
        session.flush()
        return stay_values(stay)
