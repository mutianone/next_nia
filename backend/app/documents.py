"""Local plain-text document templates, immutable snapshots and unsigned exports."""

from contextlib import contextmanager
from datetime import date, datetime
from io import BytesIO
import re
from typing import Literal
from uuid import uuid4
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, case, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.orm.exc import StaleDataError

from .database import AuditEvent, Base
from .people import Person, PersonStay, business_today, timestamp, utc_now


VARIABLES = [
    ("person_name", "姓名", "person"), ("person_code", "人员编号", "person"),
    ("nationality", "国籍", "person"), ("document_number", "证件号码", "person"),
    ("entry_date", "入境日期", "stay"), ("permitted_until", "原登记准许停留截止日期", "stay"),
    ("exit_date", "离境日期", "stay"), ("organization", "承办单位", "manual"),
    ("document_date", "文书日期", "manual"), ("document_number_label", "文书编号", "manual"),
    ("deadline", "人工核定期限", "manual"), ("facts", "事实说明", "manual"),
    ("legal_basis", "人工核对的法律依据", "manual"), ("decision", "人工填写的处理内容", "manual"),
    ("officer", "承办人员", "manual"), ("review_note", "正文中的复核说明", "manual"),
]
VARIABLE_NAMES = {item[0] for item in VARIABLES}
MANUAL_NAMES = {item[0] for item in VARIABLES if item[2] == "manual"}
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
DRAFT_LABEL = "草稿·待人工审核，不得作为正式文书使用"
REVIEWED_LABEL = "人工核对稿·未签发"
INTERNAL_ID = "3f5f7072-348a-49f5-a3d4-80c4341f5352"
INTERNAL_NAME = "内部核查工作单（非执法决定）"


class DocumentTemplate(Base):
    __tablename__ = "document_templates"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_document_template_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    source: Mapped[str] = mapped_column(String(2000), default="")
    body: Mapped[str] = mapped_column(Text)
    review_note: Mapped[str] = mapped_column(String(4000), default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __mapper_args__ = {"version_id_col": revision}


class DocumentDraft(Base):
    __tablename__ = "document_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    template_id: Mapped[str] = mapped_column(ForeignKey("document_templates.id"), index=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), index=True)
    stay_id: Mapped[str | None] = mapped_column(ForeignKey("person_stays.id"), nullable=True)
    alert_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    copied_from: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    template_snapshot: Mapped[dict] = mapped_column(JSON)
    person_snapshot: Mapped[dict] = mapped_column(JSON)
    fields: Mapped[dict] = mapped_column(JSON)
    review_note: Mapped[str] = mapped_column(String(4000), default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __mapper_args__ = {"version_id_col": revision}


class DocumentHistory(Base):
    __tablename__ = "document_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("document_drafts.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(String(4000), default="")
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


def plain_text(value: str) -> str:
    if INVALID_XML.search(value):
        raise ValueError("文本包含不支持的控制字符")
    return value


def template_variables(body: str) -> list[str]:
    variables = list(dict.fromkeys(PLACEHOLDER.findall(body)))
    remainder = PLACEHOLDER.sub("", body)
    if "{{" in remainder or "}}" in remainder or "{%" in body or "{#" in body:
        raise ValueError("模板仅支持清单内的双花括号变量，不支持表达式或脚本")
    if any(name not in VARIABLE_NAMES for name in variables):
        raise ValueError("模板包含未支持的变量，请使用变量清单")
    if len(PLACEHOLDER.findall(body)) > 200:
        raise ValueError("模板占位符总数不能超过200个")
    return variables


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="after")
    @classmethod
    def safe_strings(cls, value):
        return plain_text(value) if isinstance(value, str) else value


class TemplateCreate(InputModel):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=80)
    kind: Literal["internal", "official"]
    source: str = Field(default="", max_length=2000)
    body: str = Field(min_length=1, max_length=50000)
    review_note: str = Field(default="", max_length=4000)

    @field_validator("body")
    @classmethod
    def safe_template(cls, value):
        template_variables(value)
        return value


class TemplateUpdate(InputModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    version: str | None = Field(default=None, min_length=1, max_length=80)
    kind: Literal["internal", "official"] | None = None
    source: str | None = Field(default=None, max_length=2000)
    body: str | None = Field(default=None, min_length=1, max_length=50000)
    review_note: str | None = Field(default=None, max_length=4000)
    status: Literal["draft", "approved", "archived"] | None = None
    confirmed: bool = False

    @field_validator("body")
    @classmethod
    def safe_template(cls, value):
        if value is not None:
            template_variables(value)
        return value


def valid_fields(value: dict[str, str]) -> dict[str, str]:
    if any(key not in MANUAL_NAMES for key in value):
        raise ValueError("仅可填写清单中的人工字段，人员和停留事实来自只读快照")
    if any(len(item) > 6000 for item in value.values()) or sum(map(len, value.values())) > 40000:
        raise ValueError("人工字段每项最多6000字，合计最多40000字")
    for key, item in value.items():
        plain_text(item)
        if key in {"document_date", "deadline"} and item:
            try:
                parsed = date.fromisoformat(item)
                if parsed.isoformat() != item:
                    raise ValueError()
            except ValueError:
                raise ValueError("文书日期和人工核定期限必须使用 YYYY-MM-DD") from None
    return value


class DraftCreate(InputModel):
    template_id: str = Field(min_length=1, max_length=36)
    person_id: str = Field(min_length=1, max_length=36)
    stay_id: str | None = Field(default=None, min_length=1, max_length=36)
    alert_id: str | None = Field(default=None, min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=200)
    fields: dict[str, str] = Field(default_factory=dict)

    @field_validator("fields")
    @classmethod
    def safe_fields(cls, value):
        return valid_fields(value)


class DraftUpdate(InputModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    fields: dict[str, str] | None = None
    status: Literal["draft", "reviewed", "archived"] | None = None
    review_note: str | None = Field(default=None, max_length=4000)
    confirmed: bool = False

    @field_validator("fields")
    @classmethod
    def safe_fields(cls, value):
        return valid_fields(value) if value is not None else value


LABELS = {"name": "模板名称", "version": "版本", "body": "模板正文", "source": "来源", "review_note": "复核意见", "title": "文书标题", "expected_revision": "当前版本号", "fields": "人工字段", "status": "状态", "q": "搜索词"}
CUSTOM_ERRORS = {"文本包含不支持的控制字符", "模板仅支持清单内的双花括号变量，不支持表达式或脚本", "模板包含未支持的变量，请使用变量清单", "模板占位符总数不能超过200个", "仅可填写清单中的人工字段，人员和停留事实来自只读快照", "人工字段每项最多6000字，合计最多40000字", "文书日期和人工核定期限必须使用 YYYY-MM-DD"}


class DocumentRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError as error:
                messages = []
                for item in error.errors()[:4]:
                    custom = str(item.get("ctx", {}).get("error", ""))
                    label = next((LABELS[part] for part in reversed(item.get("loc", ())) if part in LABELS), "请求字段")
                    message = custom if custom in CUSTOM_ERRORS else ("请求包含未支持或只读字段" if item["type"] == "extra_forbidden" else f"{label}格式、长度或取值不正确，请检查后重试")
                    if message not in messages:
                        messages.append(message)
                raise HTTPException(422, "；".join(messages)) from None
            except (StaleDataError, IntegrityError):
                raise HTTPException(409, "记录已变化或模板名称与版本重复，请刷新后重试；未保存此次修改。") from None
            except SQLAlchemyError:
                raise HTTPException(503, "文书数据库暂时无法读取或保存；未完成的修改已撤销。") from None

        return safe_handler


router = APIRouter(prefix="/api/documents", tags=["本地文书"], route_class=DocumentRoute)


@contextmanager
def write_session(engine):
    with Session(engine) as session:
        # SQLite has no row locks: reserve its write lock before reading a revision.
        if engine.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


def get_record(session, model, identifier, *, lock=False):
    query = select(model).where(model.id == identifier)
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, "未找到对应模板或文书记录。")
    return row


def check_revision(row, expected):
    if row.revision != expected:
        raise HTTPException(409, "记录已被其他操作更新，请刷新后再修改。")


def audit(session, action, **identifiers):
    session.add(AuditEvent(action=action, details=identifiers))


def record_history(session, row, action):
    session.flush()
    session.add(DocumentHistory(document_id=row.id, action=action, note=row.review_note if action == "reviewed" else "", revision=row.revision, status=row.status))
    audit(session, "document." + action, document_id=row.id, revision=row.revision)
    session.flush()


def template_json(row):
    return {**{key: getattr(row, key) for key in ("id", "name", "version", "kind", "status", "source", "body", "review_note", "revision")},
            "created_at": timestamp(row.created_at), "updated_at": timestamp(row.updated_at), "variables": template_variables(row.body)}


def document_json(row):
    return {**{key: getattr(row, key) for key in ("id", "template_id", "person_id", "stay_id", "alert_id", "copied_from", "title", "status", "revision", "review_note")},
            "template_name": row.template_snapshot["name"], "template_version": row.template_snapshot["version"], "template_kind": row.template_snapshot["kind"],
            "person_name": row.person_snapshot["name"], "person_code": row.person_snapshot["code"],
            "reviewed_at": timestamp(row.reviewed_at) if row.reviewed_at else None,
            "created_at": timestamp(row.created_at), "updated_at": timestamp(row.updated_at)}


def render(row):
    person = row.person_snapshot
    stay = person.get("stay") or {}
    values = {"person_name": person["name"], "person_code": person["code"], "nationality": person["nationality"],
              "document_number": person["document_number"], **{key: stay.get(key) or "" for key in ("entry_date", "permitted_until", "exit_date")}, **row.fields}
    if any(INVALID_XML.search(value) for value in values.values()):
        raise HTTPException(422, "人员快照或人工字段包含不支持的控制字符，请核对原始资料。")
    variables = template_variables(row.template_snapshot["body"])
    unresolved = [name for name in variables if not values.get(name, "").strip()]
    rendered = PLACEHOLDER.sub(lambda match: values.get(match[1], "") or "{{" + match[1] + "}}", row.template_snapshot["body"])
    if len(rendered) > 250000:
        raise HTTPException(422, "生成正文过长，请缩短模板或人工字段。")
    return rendered, unresolved


def full_document(session, row):
    rendered, unresolved = render(row)
    history = session.scalars(select(DocumentHistory).where(DocumentHistory.document_id == row.id).order_by(DocumentHistory.created_at, DocumentHistory.id)).all()
    return {**document_json(row), "template_snapshot": row.template_snapshot, "person_snapshot": row.person_snapshot, "fields": row.fields,
            "rendered_text": rendered, "unresolved_fields": unresolved, "export_label": REVIEWED_LABEL if row.status == "reviewed" else DRAFT_LABEL,
            "history": [{"id": item.id, "action": item.action, "note": item.note, "revision": item.revision, "status": item.status, "created_at": timestamp(item.created_at)} for item in history]}


def complete_document(row):
    rendered, unresolved = render(row)
    if unresolved:
        raise HTTPException(422, "正文仍有未填写的必需变量，请补齐后再审核或导出；人员或停留快照缺项时，请完善档案后重新创建文书。")
    if row.fields.get("deadline") and row.fields.get("document_date") and row.fields["deadline"] < row.fields["document_date"]:
        raise HTTPException(422, "人工核定期限不能早于文书日期，请核对后重试。")
    return rendered


def seed_templates(engine):
    """Create one explicitly internal worksheet; never overwrite an existing row."""
    with write_session(engine) as session:
        if session.scalar(select(DocumentTemplate.id).where(or_(DocumentTemplate.id == INTERNAL_ID, (DocumentTemplate.name == INTERNAL_NAME) & (DocumentTemplate.version == "1.0")))):
            return
        session.add(DocumentTemplate(id=INTERNAL_ID, name=INTERNAL_NAME, version="1.0", kind="internal", status="approved",
            source="系统内置内部整理样式；不是法定文书模板，不是执法决定。", review_note="仅供内部事实核对和材料整理，不能代替业务审核、法定文书或签发。",
            body="内部核查工作单（非执法决定）\n本工作单仅用于内部事实核对，不作违法认定或执法决定。\n\n姓名：{{person_name}}\n人员编号：{{person_code}}\n国籍：{{nationality}}\n证件号码：{{document_number}}\n入境日期：{{entry_date}}\n原登记准许停留截止日期：{{permitted_until}}\n\n事实说明：\n{{facts}}\n\n承办单位：{{organization}}\n承办人员：{{officer}}\n文书日期：{{document_date}}"))
        audit(session, "document.template_seeded", template_id=INTERNAL_ID)


@router.get("/templates/variables")
def list_variables():
    return {"items": [{"name": name, "label": label, "source": source} for name, label, source in VARIABLES]}


@router.get("/templates")
def list_templates(request: Request):
    with Session(request.app.state.engine) as session:
        return {"items": [template_json(row) for row in session.scalars(select(DocumentTemplate).order_by(DocumentTemplate.created_at.desc(), DocumentTemplate.id))]}


@router.post("/templates", status_code=201)
def create_template(data: TemplateCreate, request: Request):
    with write_session(request.app.state.engine) as session:
        row = DocumentTemplate(**data.model_dump())
        session.add(row)
        session.flush()
        audit(session, "document.template_created", template_id=row.id, revision=row.revision)
        return template_json(row)


@router.get("/templates/{template_id}")
def read_template(template_id: str, request: Request):
    with Session(request.app.state.engine) as session:
        return template_json(get_record(session, DocumentTemplate, template_id))


@router.patch("/templates/{template_id}")
def update_template(template_id: str, data: TemplateUpdate, request: Request):
    with write_session(request.app.state.engine) as session:
        row = get_record(session, DocumentTemplate, template_id, lock=True)
        check_revision(row, data.expected_revision)
        changes = data.model_dump(exclude_unset=True, exclude={"expected_revision", "confirmed"})
        if any(value is None for value in changes.values()):
            raise HTTPException(422, "模板字段不能设为 null，请填写有效内容。")
        if row.status == "archived":
            raise HTTPException(409, "已归档模板只读，请创建新版本。")
        if row.status == "approved" and changes != {"status": "archived"}:
            raise HTTPException(409, "已批准模板内容不可修改，只能归档；修改请创建新版本。")
        for key, value in changes.items():
            setattr(row, key, value)
        if row.status == "approved":
            if not data.confirmed or not row.source.strip() or not row.review_note.strip():
                raise HTTPException(422, "批准模板须填写已核对的来源和复核意见，并明确确认。正式业务模板需由业务方核实标准文案。")
        row.updated_at = utc_now()
        session.flush()
        audit(session, "document.template_updated", template_id=row.id, revision=row.revision)
        return template_json(row)


@router.get("")
def list_documents(request: Request, q: str = Query(default="", max_length=200), status: Literal["draft", "reviewed", "archived", "all"] = "all", page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100)):
    with Session(request.app.state.engine) as session:
        conditions = []
        if status != "all":
            conditions.append(DocumentDraft.status == status)
        if q.strip():
            conditions.append(or_(DocumentDraft.title.icontains(q.strip(), autoescape=True),
                DocumentDraft.person_snapshot["name"].as_string().icontains(q.strip(), autoescape=True),
                DocumentDraft.person_snapshot["code"].as_string().icontains(q.strip(), autoescape=True)))
        total = session.scalar(select(func.count()).select_from(DocumentDraft).where(*conditions)) or 0
        rows = session.scalars(select(DocumentDraft).where(*conditions).order_by(DocumentDraft.updated_at.desc(), DocumentDraft.id).offset((page - 1) * page_size).limit(page_size)).all()
        counts = session.execute(select(func.count(DocumentDraft.id), *[func.coalesce(func.sum(case((DocumentDraft.status == value, 1), else_=0)), 0) for value in ("draft", "reviewed", "archived")])).one()
        return {"items": [document_json(row) for row in rows], "total": total, "page": page, "page_size": page_size,
                "stats": dict(zip(("total", "drafts", "reviewed", "archived"), counts))}


@router.post("", status_code=201)
def create_document(data: DraftCreate, request: Request):
    with write_session(request.app.state.engine) as session:
        template = get_record(session, DocumentTemplate, data.template_id, lock=True)
        if template.status == "archived":
            raise HTTPException(409, "已归档模板不能新建文书，请选择有效模板。")
        person = session.scalar(select(Person).where(Person.id == data.person_id).with_for_update())
        if person is None or person.archived:
            raise HTTPException(422, "请选择未归档的现有人员档案。")
        stay = None
        if data.stay_id:
            stay = session.scalar(select(PersonStay).where(PersonStay.id == data.stay_id, PersonStay.person_id == person.id).with_for_update())
            if stay is None:
                raise HTTPException(422, "停留记录不存在或不属于所选人员。")
        if data.alert_id:
            from .alerts import Alert
            alert = session.scalar(select(Alert).where(Alert.id == data.alert_id).with_for_update())
            if alert is None or alert.person_id != person.id or alert.stay_id != data.stay_id:
                raise HTTPException(422, "预警记录不存在或与所选人员、停留记录不一致。")
        snapshot = {key: getattr(person, key) for key in ("id", "code", "name", "nationality", "document_number", "verification", "source", "archived")}
        snapshot["captured_at"] = timestamp(utc_now())
        snapshot["stay"] = ({"id": stay.id, "source": stay.source, **{key: getattr(stay, key).isoformat() if getattr(stay, key) else None for key in ("entry_date", "permitted_until", "exit_date")}} if stay else None)
        row = DocumentDraft(template_id=template.id, person_id=person.id, stay_id=data.stay_id, alert_id=data.alert_id,
            title=data.title, fields={"document_date": business_today().isoformat(), **data.fields}, template_snapshot=template_json(template), person_snapshot=snapshot)
        session.add(row)
        record_history(session, row, "created")
        return full_document(session, row)


@router.get("/{document_id}")
def read_document(document_id: str, request: Request):
    with Session(request.app.state.engine) as session:
        return full_document(session, get_record(session, DocumentDraft, document_id))


@router.patch("/{document_id}")
def update_document(document_id: str, data: DraftUpdate, request: Request):
    with write_session(request.app.state.engine) as session:
        row = get_record(session, DocumentDraft, document_id, lock=True)
        check_revision(row, data.expected_revision)
        changes = data.model_dump(exclude_unset=True, exclude={"expected_revision", "confirmed"})
        if any(value is None for value in changes.values()):
            raise HTTPException(422, "文书字段不能设为 null，请填写有效内容。")
        if row.status == "archived":
            raise HTTPException(409, "已归档文书只读，可复制为新草稿。")
        if row.status == "reviewed" and changes != {"status": "archived"}:
            raise HTTPException(409, "已审核文书不可修改正文或审核记录，可复制为新草稿。")
        for key, value in changes.items():
            setattr(row, key, value)
        action = "updated"
        if row.status == "reviewed":
            template = get_record(session, DocumentTemplate, row.template_id, lock=True)
            if template.status != "approved":
                raise HTTPException(409, "源模板当前未批准或已归档，不能审核该草稿。")
            if any(getattr(template, key) != row.template_snapshot[key] for key in ("name", "version", "kind", "source", "body")):
                raise HTTPException(409, "源模板内容与创建时快照不同，请使用当前模板重新创建文书。")
            complete_document(row)
            if not data.confirmed or not row.review_note.strip():
                raise HTTPException(422, "审核须填写复核意见，并确认已人工核对人员快照、事实、依据和正文；系统不作法律正确性判断。")
            row.reviewed_at = utc_now()
            action = "reviewed"
        elif row.status == "archived":
            action = "archived"
        row.updated_at = utc_now()
        record_history(session, row, action)
        return full_document(session, row)


@router.post("/{document_id}/copy", status_code=201)
def copy_document(document_id: str, request: Request):
    with write_session(request.app.state.engine) as session:
        original = get_record(session, DocumentDraft, document_id, lock=True)
        row = DocumentDraft(template_id=original.template_id, person_id=original.person_id, stay_id=original.stay_id, alert_id=original.alert_id,
            copied_from=original.id, title=(original.title[:195] + "（副本）"), fields=dict(original.fields),
            template_snapshot=dict(original.template_snapshot), person_snapshot=dict(original.person_snapshot))
        session.add(row)
        record_history(session, row, "copied")
        return full_document(session, row)


def paragraph(value, *, bold=False, color=None, size=24, centered=False, style=None):
    properties = '<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="宋体"/>'
    if bold:
        properties += "<w:b/>"
    if color:
        properties += f'<w:color w:val="{color}"/>'
    properties += f'<w:sz w:val="{size}"/>'
    paragraph_properties = (f'<w:pStyle w:val="{style}"/>' if style else "") + '<w:widowControl/><w:spacing w:after="160" w:line="360" w:lineRule="auto"/>' + ('<w:jc w:val="center"/>' if centered else "")
    return '<w:p><w:pPr>' + paragraph_properties + '</w:pPr><w:r><w:rPr>' + properties + '</w:rPr><w:t xml:space="preserve">' + escape(value) + '</w:t></w:r></w:p>'


def make_docx(title, body, label):
    # Fixed archive members and no external relationships, macros or HTML parts.
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    parts = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/_rels/document.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        "word/styles.xml": '<w:styles ' + ns + '><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="宋体"/><w:sz w:val="24"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:jc w:val="center"/></w:pPr><w:rPr><w:b/><w:color w:val="000000"/><w:sz w:val="32"/></w:rPr></w:style></w:styles>',
        "word/header1.xml": '<w:hdr ' + ns + '>' + paragraph(label, bold=True, color="B42318", size=20, centered=True) + '</w:hdr>',
        "word/document.xml": '<w:document ' + ns + '><w:body>' + paragraph(title, bold=True, color="000000", size=32, centered=True, style="Title") + paragraph(label, bold=True, color="B42318") + ''.join(paragraph(line) for line in body.splitlines()) + '<w:sectPr><w:headerReference w:type="default" r:id="rId1"/><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1134" w:bottom="1134" w:left="1134" w:header="360" w:footer="360"/></w:sectPr></w:body></w:document>',
    }
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + content).encode("utf-8"))
    return output.getvalue()


def export_document(document_id, request, format):
    with write_session(request.app.state.engine) as session:
        row = get_record(session, DocumentDraft, document_id, lock=True)
        if row.status == "archived":
            raise HTTPException(409, "已归档文书不能导出；如需继续整理，请复制为新草稿。")
        body = complete_document(row)
        label = REVIEWED_LABEL if row.status == "reviewed" else DRAFT_LABEL
        if format == "docx":
            content = make_docx(row.title, body, label)
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            content = (label + "\n\n" + row.title + "\n\n" + body + "\n\n" + label + "\n").encode("utf-8-sig")
            content_type = "text/plain; charset=utf-8"
        audit(session, "document.exported", document_id=row.id, revision=row.revision)
        return Response(content=content, media_type=content_type, headers={"Content-Disposition": f'attachment; filename="document.{format}"', "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.get("/{document_id}/export.txt")
def export_txt(document_id: str, request: Request):
    return export_document(document_id, request, "txt")


@router.get("/{document_id}/export.docx")
def export_docx(document_id: str, request: Request):
    return export_document(document_id, request, "docx")
