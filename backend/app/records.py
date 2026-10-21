"""Local originals and revision snapshots; all external AI needs confirmation."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.orm.exc import StaleDataError

from .database import AuditEvent, Base
from .documents import DRAFT_LABEL, REVIEWED_LABEL, make_docx, plain_text
from .people import Person, timestamp, utc_now
from .record_media import conversion_available
from .records_ai import RecordAIError, analyze_segments, provider_config, transcribe_audio
from .schemas import reveal

Scenario = Literal["study", "marriage", "residence", "violation", "other"]
Speaker = Literal["interviewer", "interviewee", "interpreter", "unknown"]
LIMITS = {"max_audio_bytes": 20 * 1024 * 1024, "max_record_audio_bytes": 500 * 1024 * 1024,
          "max_clips": 30, "max_segment_chars": 20000, "max_total_text_chars": 100000,
          "max_segments": 500, "max_analyze_chars": 30000, "max_duration_ms": 86400000,
          "asr_max_duration_ms": 3600000, "formats": ["wav", "mp3", "webm", "ogg", "m4a", "flac"]}
TEMPLATES = [{"id": key, "name": name, "description": description, "version": "1"} for key, name, description in [
    ("study", "来华求学询问整理稿", "整理求学事项，不自动判断资格。"),
    ("marriage", "婚姻家庭询问整理稿", "整理已陈述事项，不推断婚姻真实性。"),
    ("residence", "居留事由核查询问整理稿", "记录事由陈述，不自动作出许可结论。"),
    ("violation", "涉出入境事项询问整理稿", "记录原始陈述，不自动作出违法、处罚或可信度判断。"),
    ("other", "通用询问整理稿", "按修订顺序编排陈述，供人工核对。"),
]]
SPEAKERS = {"interviewer": "询问人", "interviewee": "被询问人", "interpreter": "翻译人员", "unknown": "说话人未标注"}


class RecordSession(Base):
    __tablename__ = "record_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(200))
    scenario: Mapped[str] = mapped_column(String(20))
    template_id: Mapped[str] = mapped_column(String(20))
    template_snapshot: Mapped[dict] = mapped_column(JSON)
    person_id: Mapped[str | None] = mapped_column(ForeignKey("people.id"), nullable=True)
    person_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    location: Mapped[str] = mapped_column(String(500), default="")
    interviewers: Mapped[str] = mapped_column(String(500), default="")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    archived_from: Mapped[str | None] = mapped_column(String(16), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    review_note: Mapped[str] = mapped_column(String(4000), default="")
    reviewed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __mapper_args__ = {"version_id_col": revision}


class AudioClip(Base):
    __tablename__ = "record_audio"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    record_id: Mapped[str] = mapped_column(ForeignKey("record_sessions.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    format: Mapped[str] = mapped_column(String(10))
    mime_type: Mapped[str] = mapped_column(String(60))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_audio: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RecordSegment(Base):
    __tablename__ = "record_segments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    record_id: Mapped[str] = mapped_column(ForeignKey("record_sessions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    audio_id: Mapped[str | None] = mapped_column(ForeignKey("record_audio.id"), nullable=True)
    start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    speaker: Mapped[str] = mapped_column(String(16), default="unknown")
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10), default="manual")
    transcription_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class RecordVersion(Base):
    __tablename__ = "record_versions"
    __table_args__ = (UniqueConstraint("record_id", "revision", name="uq_record_revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    record_id: Mapped[str] = mapped_column(ForeignKey("record_sessions.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text, default="")
    snapshot: Mapped[dict] = mapped_column(JSON, deferred=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TranscriptionAttempt(Base):
    __tablename__ = "record_transcription_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    record_id: Mapped[str] = mapped_column(ForeignKey("record_sessions.id"), index=True)
    audio_id: Mapped[str] = mapped_column(ForeignKey("record_audio.id"))
    status: Mapped[str] = mapped_column(String(16), default="processing")
    text: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(String(400), default="")
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(200))
    input_revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RecordAnalysis(Base):
    __tablename__ = "record_analyses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    record_id: Mapped[str] = mapped_column(ForeignKey("record_sessions.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="processing")
    summary: Mapped[str] = mapped_column(Text, default="")
    key_facts: Mapped[list] = mapped_column(JSON, default=list)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    input_segments: Mapped[list] = mapped_column(JSON)
    input_hash: Mapped[str] = mapped_column(String(64))
    input_revision: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(200))
    error: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="after")
    @classmethod
    def safe_text(cls, value):
        return plain_text(value) if isinstance(value, str) else value

    @field_validator("occurred_at", mode="after", check_fields=False)
    @classmethod
    def explicit_timezone(cls, value):
        if value and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("询问发生时间须含时区；不清楚时可留空")
        return value.astimezone(timezone.utc) if value else None


class RecordCreate(Input):
    title: str = Field(min_length=1, max_length=200)
    scenario: Scenario = "other"
    template_id: Scenario | None = None
    person_id: str | None = Field(default=None, min_length=1, max_length=36)
    location: str = Field(default="", max_length=500)
    interviewers: str = Field(default="", max_length=500)
    occurred_at: datetime | None = None


class RecordUpdate(Input):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    scenario: Scenario | None = None
    template_id: Scenario | None = None
    location: str | None = Field(default=None, max_length=500)
    interviewers: str | None = Field(default=None, max_length=500)
    occurred_at: datetime | None = None
    status: Literal["draft", "reviewed", "archived"] | None = None
    review_note: str | None = Field(default=None, max_length=4000)
    confirmed: bool = Field(default=False, strict=True)


class SegmentInput(Input):
    id: str | None = Field(default=None, min_length=1, max_length=36)
    audio_id: str | None = Field(default=None, min_length=1, max_length=36)
    start_ms: int | None = Field(default=None, ge=0, le=86400000, strict=True)
    end_ms: int | None = Field(default=None, ge=1, le=86400000, strict=True)
    speaker: Speaker = "unknown"
    text: str = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def valid_range(self):
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("片段起止位置须同时填写或同时留空")
        if self.start_ms is not None and (not self.audio_id or self.end_ms <= self.start_ms):
            raise ValueError("录音片段需关联原音，结束位置须晚于开始位置")
        return self


class SegmentsUpdate(Input):
    expected_revision: int = Field(ge=1)
    segments: list[SegmentInput] = Field(max_length=500)

    @field_validator("segments")
    @classmethod
    def bounded_text(cls, value):
        if sum(len(item.text) for item in value) > 100000:
            raise ValueError("当前片段总文本不能超过100000字")
        return value


class AIRequest(Input):
    expected_revision: int = Field(ge=1)
    confirmed: bool = Field(default=False, strict=True)


CUSTOM_ERRORS = {"文本包含不支持的控制字符", "询问发生时间须含时区；不清楚时可留空", "片段起止位置须同时填写或同时留空", "录音片段需关联原音，结束位置须晚于开始位置", "当前片段总文本不能超过100000字"}


class RecordsRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def safe(request: Request):
            try:
                if request.method in ("POST", "PATCH", "PUT"):
                    limit = LIMITS["max_audio_bytes"] + 65536 if request.url.path.endswith("/audio") else 1024 * 1024
                    received = bytearray()
                    async for chunk in request.stream():
                        received.extend(chunk)
                        if len(received) > limit:
                            raise HTTPException(413, "请求超过允许大小；音频每段最多20MiB，请拆分完整录音后上传。")
                    async def receive():
                        return {"type": "http.request", "body": bytes(received), "more_body": False}
                    request = Request(request.scope, receive)
                return await original(request)
            except RequestValidationError as error:
                messages = []
                for item in error.errors()[:4]:
                    custom = str(item.get("ctx", {}).get("error", ""))
                    message = custom if custom in CUSTOM_ERRORS else "笔录请求字段格式、长度或取值不正确，请检查后重试。"
                    if message not in messages:
                        messages.append(message)
                raise HTTPException(422, "；".join(messages)) from None
            except (StaleDataError, IntegrityError):
                raise HTTPException(409, "笔录记录已被其他操作更新或发生冲突；本次修改未保存，请重新读取。") from None
            except SQLAlchemyError:
                raise HTTPException(503, "笔录数据库操作未完成，本次未完成修改已撤销；请检查服务后重试。") from None
        return safe


router = APIRouter(prefix="/api/records", tags=["本地录音与询问笔录"], route_class=RecordsRoute)


@contextmanager
def write_session(engine):
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        try:
            if engine.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


def get_record(session, record_id, lock=False):
    statement = select(RecordSession).where(RecordSession.id == record_id)
    row = session.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise HTTPException(404, "未找到该笔录记录。")
    return row


def mutable(row, expected):
    if row.revision != expected:
        raise HTTPException(409, "笔录已被其他操作修改；请重新读取并核对，未保存本次修改。")
    if row.status != "draft":
        raise HTTPException(409, "已核对或已归档笔录不可修改内容，原音和历史仍可查阅。")


def audit(session, action, **ids_counts):
    session.add(AuditEvent(action="records." + action, details=ids_counts))


def ordered_segments(session, record_id):
    return session.scalars(select(RecordSegment).where(RecordSegment.record_id == record_id).order_by(RecordSegment.position, RecordSegment.id)).all()


def segment_json(row):
    return {**{key: getattr(row, key) for key in ("id", "audio_id", "start_ms", "end_ms", "speaker", "text", "source", "transcription_id")},
            "timestamp_source": "manual" if row.start_ms is not None else "none"}


def clip_json(row):
    return {**{key: getattr(row, key) for key in ("id", "filename", "format", "mime_type", "size_bytes", "sha256", "duration_ms")},
            "duration_source": "declared" if row.duration_ms is not None else "unknown", "created_at": timestamp(row.created_at),
            "playback_url": f"/api/records/{row.record_id}/audio/{row.id}/file"}


def record_json(session, row):
    return {**{key: getattr(row, key) for key in ("id", "title", "scenario", "template_id", "person_id", "location", "interviewers", "status", "archived_from", "revision", "review_note")},
            "person_name": (row.person_snapshot or {}).get("name"), "person_code": (row.person_snapshot or {}).get("code"),
            "occurred_at": timestamp(row.occurred_at) if row.occurred_at else None,
            "reviewed_at": timestamp(row.reviewed_at) if row.reviewed_at else None,
            "created_at": timestamp(row.created_at), "updated_at": timestamp(row.updated_at),
            "audio_count": session.scalar(select(func.count()).select_from(AudioClip).where(AudioClip.record_id == row.id)) or 0,
            "segment_count": session.scalar(select(func.count()).select_from(RecordSegment).where(RecordSegment.record_id == row.id)) or 0}


def input_segments(segments):
    return [{"segment_id": item.id, "text": item.text} for item in segments]


def content_hash(segments):
    return hashlib.sha256(json.dumps(segments, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def segment_hash(segments):
    # Local provenance is more conservative than the outbound text-only input:
    # changed speaker labels, clip associations or positions also require review.
    return content_hash([segment_json(item) for item in segments])


def render_text(row, segments):
    if row.reviewed_text is not None:
        return row.reviewed_text
    happened = datetime.fromisoformat(timestamp(row.occurred_at)).astimezone(ZoneInfo("Asia/Shanghai")).isoformat() if row.occurred_at else "未填写（创建时间不是询问发生时间）"
    person = row.person_snapshot or {}
    lines = [row.template_snapshot["name"], "本稿仅整理当前人工修订片段，不自动作法律或可信度判断。", f"询问发生时间：{happened}", f"地点：{row.location or '未填写'}", f"询问人：{row.interviewers or '未填写'}"]
    if person:
        lines.extend([f"关联人员：{person['name']}", f"档案编号：{person['code']}（建稿时快照）"])
    lines.append("")
    for number, item in enumerate(segments, 1):
        location = f"；人工标注原音内位置 {item.start_ms}–{item.end_ms} 毫秒" if item.start_ms is not None else ""
        lines.extend([f"片段 {number} [{item.id}] · {SPEAKERS[item.speaker]}{location}", item.text, ""])
    return "\n".join(lines)


def detail(session, row):
    segments = ordered_segments(session, row.id)
    digest = segment_hash(segments)
    analyses = [analysis_json(item, digest) for item in session.scalars(select(RecordAnalysis).where(RecordAnalysis.record_id == row.id).order_by(RecordAnalysis.created_at.desc(), RecordAnalysis.id))]
    attempts = session.scalars(select(TranscriptionAttempt).where(TranscriptionAttempt.record_id == row.id).order_by(TranscriptionAttempt.created_at.desc(), TranscriptionAttempt.id)).all()
    versions = session.scalars(select(RecordVersion).where(RecordVersion.record_id == row.id).order_by(RecordVersion.revision)).all()
    return {**record_json(session, row), "person_snapshot": row.person_snapshot, "template_snapshot": row.template_snapshot, "segments": [segment_json(item) for item in segments],
            "audio_clips": [clip_json(item) for item in session.scalars(select(AudioClip).where(AudioClip.record_id == row.id).order_by(AudioClip.created_at, AudioClip.id))],
            "transcription_attempts": [{**{key: getattr(item, key) for key in ("id", "audio_id", "status", "text", "error", "provider", "model", "input_revision")}, "created_at": timestamp(item.created_at), "finished_at": timestamp(item.finished_at) if item.finished_at else None} for item in attempts],
            "analyses": analyses, "analysis": next((item for item in analyses if item["status"] == "succeeded"), None),
            "history": [{"id": item.id, "revision": item.revision, "action": item.action, "note": item.note, "created_at": timestamp(item.created_at)} for item in versions],
            "rendered_text": render_text(row, segments)}


def analysis_json(row, current_hash):
    return {**{key: getattr(row, key) for key in ("id", "status", "summary", "key_facts", "issues", "input_revision", "input_segments", "provider", "model", "error")},
            "stale": row.status == "stale" or row.input_hash != current_hash,
            "created_at": timestamp(row.created_at), "finished_at": timestamp(row.finished_at) if row.finished_at else None}


def version(session, row, action, *, initial=False):
    if not initial:
        row.revision += 1
        row.updated_at = utc_now()
    session.flush()
    segments = ordered_segments(session, row.id)
    snapshot = {**record_json(session, row), "person_snapshot": row.person_snapshot, "template_snapshot": row.template_snapshot,
                "segments": [segment_json(item) for item in segments], "rendered_text": render_text(row, segments),
                "audio_clips": [clip_json(item) for item in session.scalars(select(AudioClip).where(AudioClip.record_id == row.id))]}
    session.add(RecordVersion(record_id=row.id, revision=row.revision, action=action, note=row.review_note if action == "reviewed" else "", snapshot=snapshot))
    audit(session, action, record_id=row.id, revision=row.revision)
    session.flush()


def settings_copy(request):
    with request.app.state.store.lock:
        return request.app.state.store.current.model_copy(deep=True)


@router.get("/capabilities")
def capabilities(request: Request):
    settings = settings_copy(request)
    providers = {}
    for purpose in ("asr", "analysis"):
        name = "siliconflow" if purpose == "asr" else settings.ai.default_provider
        configured = getattr(settings.ai.providers, name)
        providers[purpose] = {"provider": name, "model": configured.asr_model if purpose == "asr" else configured.chat_model,
                              "ready": settings.ai.enabled and configured.enabled and bool(reveal(configured.api_key))}
    available = conversion_available()
    providers["asr"].update({"format_support_verified": False, "local_conversion_available": available,
                            "format_note": "原音本地留存；MP3直接发送，其他格式需本机FFmpeg先转MP3，原件不变。未进行真实在线验证；声明时长不是服务端实测时长。"})
    return {"limits": LIMITS, "templates": TEMPLATES, "local_conversion_available": available, "ai": {"enabled": settings.ai.enabled, **providers}}


@router.get("")
def list_records(request: Request, q: str = Query(default="", max_length=200), status: Literal["all", "draft", "reviewed", "archived"] = "all", page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100)):
    with Session(request.app.state.engine) as session:
        conditions = []
        if status != "all":
            conditions.append(RecordSession.status == status)
        if q.strip():
            conditions.append(or_(RecordSession.title.icontains(q.strip(), autoescape=True), RecordSession.person_snapshot["name"].as_string().icontains(q.strip(), autoescape=True)))
        total = session.scalar(select(func.count()).select_from(RecordSession).where(*conditions)) or 0
        rows = session.scalars(select(RecordSession).where(*conditions).order_by(RecordSession.updated_at.desc(), RecordSession.id).offset((page - 1) * page_size).limit(page_size)).all()
        stats = dict(session.execute(select(RecordSession.status, func.count()).group_by(RecordSession.status)).all())
        return {"items": [record_json(session, row) for row in rows], "total": total, "page": page, "page_size": page_size,
                "stats": {"total": sum(stats.values()), **{name: stats.get(name, 0) for name in ("draft", "reviewed", "archived")}}}


@router.post("", status_code=201)
def create_record(body: RecordCreate, request: Request):
    with write_session(request.app.state.engine) as session:
        snapshot = None
        if body.person_id:
            person = session.scalar(select(Person).where(Person.id == body.person_id).with_for_update())
            if person is None or person.archived:
                raise HTTPException(422, "请选择未归档的现有人员档案，或不关联人员。")
            snapshot = {key: getattr(person, key) for key in ("id", "name", "code", "nationality", "document_number", "verification", "source")}
            snapshot["captured_at"] = timestamp(utc_now())
        data = body.model_dump()
        data["template_id"] = body.template_id or body.scenario
        row = RecordSession(**data, person_snapshot=snapshot, template_snapshot=dict(next(item for item in TEMPLATES if item["id"] == data["template_id"])))
        session.add(row)
        version(session, row, "created", initial=True)
        return detail(session, row)


@router.get("/{record_id}")
def get_detail(record_id: str, request: Request):
    with Session(request.app.state.engine) as session:
        return detail(session, get_record(session, record_id))


@router.patch("/{record_id}")
def update_record(record_id: str, body: RecordUpdate, request: Request):
    with write_session(request.app.state.engine) as session:
        row = get_record(session, record_id, True)
        if row.revision != body.expected_revision:
            raise HTTPException(409, "笔录版本已变化，请重新读取；本次修改未保存。")
        changes = body.model_dump(exclude_unset=True, exclude={"expected_revision", "confirmed"})
        if any(value is None for key, value in changes.items() if key != "occurred_at"):
            raise HTTPException(422, "除询问发生时间外，笔录字段不能设为null。")
        action = "updated"
        if row.status == "archived":
            if changes != {"status": row.archived_from}:
                raise HTTPException(409, "归档记录只允许恢复到归档前状态，不能修改或绕过审核锁定。")
            row.status, row.archived_from = row.archived_from, None
            action = "restored"
        elif changes.get("status") == "archived":
            if changes != {"status": "archived"}:
                raise HTTPException(422, "归档操作不能同时修改正文或其他资料，请先单独保存。")
            row.archived_from, row.status = row.status, "archived"
            action = "archived"
        else:
            mutable(row, body.expected_revision)
            for key, value in changes.items():
                setattr(row, key, value)
            if "scenario" in changes and "template_id" not in changes:
                row.template_id = row.scenario
                changes["template_id"] = row.template_id
            if "template_id" in changes:
                row.template_snapshot = dict(next(item for item in TEMPLATES if item["id"] == row.template_id))
            if row.status == "reviewed":
                segments = ordered_segments(session, row.id)
                if not body.confirmed or not row.review_note.strip() or not segments:
                    raise HTTPException(422, "核对须有非空正文、核对说明，并明确确认已人工核对；不自动签发。")
                row.reviewed_text = render_text(row, segments)
                row.reviewed_at, action = utc_now(), "reviewed"
        version(session, row, action)
        return detail(session, row)


@router.patch("/{record_id}/segments")
def replace_segments(record_id: str, body: SegmentsUpdate, request: Request):
    with write_session(request.app.state.engine) as session:
        row = get_record(session, record_id, True)
        mutable(row, body.expected_revision)
        existing = {item.id: item for item in ordered_segments(session, row.id)}
        clips = {item.id: item for item in session.scalars(select(AudioClip).where(AudioClip.record_id == row.id))}
        seen = set()
        for position, item in enumerate(body.segments):
            if item.id and (item.id not in existing or item.id in seen):
                raise HTTPException(422, "片段编号重复、不存在或不属于当前笔录。")
            if item.audio_id and item.audio_id not in clips:
                raise HTTPException(422, "所选原音不属于当前笔录。")
            if item.end_ms is not None and clips[item.audio_id].duration_ms is not None and item.end_ms > clips[item.audio_id].duration_ms:
                raise HTTPException(422, "片段位置超过该原音声明的时长，请核实后填写。")
            target = existing.get(item.id) if item.id else RecordSegment(id=str(uuid4()), record_id=row.id, source="manual")
            seen.add(target.id)
            for key, value in item.model_dump(exclude={"id"}).items():
                setattr(target, key, value)
            target.position = position
            session.add(target)
        for identifier, target in existing.items():
            if identifier not in seen:
                session.delete(target)
        session.flush()
        version(session, row, "segments_updated")
        return detail(session, row)


def mp4_has_audio_track(raw):
    """Inspect ISO box boundaries, not arbitrary occurrences inside media bytes."""
    pending, handlers, boxes = [(0, len(raw), 0)], set(), 0
    while pending:
        offset, end, depth = pending.pop()
        while offset + 8 <= end:
            boxes += 1
            if boxes > 10000:
                return False
            length, kind = int.from_bytes(raw[offset:offset + 4], "big"), raw[offset + 4:offset + 8]
            header = 8
            if length == 1:
                if offset + 16 > end:
                    return False
                length, header = int.from_bytes(raw[offset + 8:offset + 16], "big"), 16
            elif length == 0:
                length = end - offset
            if length < header or offset + length > end:
                return False
            if kind in (b"moov", b"trak", b"mdia") and depth < 4:
                pending.append((offset + header, offset + length, depth + 1))
            elif kind == b"hdlr" and length >= header + 12:
                handlers.add(raw[offset + header + 8:offset + header + 12])
            offset += length
    return b"soun" in handlers and b"vide" not in handlers


def detect_audio(raw):
    if len(raw) < 16:
        raise HTTPException(422, "音频文件过短或不是可识别的完整音频格式。")
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE" and b"fmt " in raw[:4096] and b"data" in raw:
        return "wav", "audio/wav"
    if (raw[:3] == b"ID3" and raw[3] in (2, 3, 4)) or (raw[0] == 255 and raw[1] & 224 == 224 and raw[1] & 6 != 0):
        return "mp3", "audio/mpeg"
    if raw[:4] == b"\x1aE\xdf\xa3" and b"webm" in raw[:128]:
        return "webm", "audio/webm"
    if raw[:4] == b"OggS" and len(raw) >= 27:
        return "ogg", "audio/ogg"
    if raw[4:8] == b"ftyp":
        brand = raw[8:12]
        if brand in (b"M4A ", b"M4B ", b"F4A ") or ((brand in (b"isom", b"mp41", b"mp42") or re.fullmatch(b"iso[2-9]", brand)) and mp4_has_audio_track(raw)):
            return "m4a", "audio/mp4"
    if raw[:4] == b"fLaC" and len(raw) >= 42:
        return "flac", "audio/flac"
    raise HTTPException(422, "未识别到支持的音频格式签名；不接受改后缀的文件、外部链接或独立无头录音分块。")


@router.post("/{record_id}/audio", status_code=201)
def upload_audio(record_id: str, request: Request, file: UploadFile = File(...), expected_revision: int = Form(..., ge=1), duration_ms: int | None = Form(default=None, ge=1, le=86400000)):
    raw = file.file.read(LIMITS["max_audio_bytes"] + 1)
    if not raw or len(raw) > LIMITS["max_audio_bytes"]:
        raise HTTPException(413, "原音不能为空，且每段不得超过20MiB。")
    file_format, mime_type = detect_audio(raw)
    filename = (file.filename or f"recording.{file_format}").replace("\\", "/").rsplit("/", 1)[-1][:255]
    try:
        plain_text(filename)
    except ValueError:
        raise HTTPException(422, "文件名包含不支持的控制字符。") from None
    with write_session(request.app.state.engine) as session:
        row = get_record(session, record_id, True)
        mutable(row, expected_revision)
        count, total = session.execute(select(func.count(), func.coalesce(func.sum(AudioClip.size_bytes), 0)).where(AudioClip.record_id == row.id)).one()
        if count >= LIMITS["max_clips"] or total + len(raw) > LIMITS["max_record_audio_bytes"]:
            raise HTTPException(413, "该笔录已达到30段原音或500MiB总容量限制，请另建笔录。")
        clip = AudioClip(record_id=row.id, filename=filename or f"recording.{file_format}", format=file_format, mime_type=mime_type, size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), duration_ms=duration_ms, raw_audio=raw)
        session.add(clip)
        session.flush()
        version(session, row, "audio_added")
        return detail(session, row)


@router.get("/{record_id}/audio/{audio_id}/file")
def audio_file(record_id: str, audio_id: str, request: Request, download: bool = False):
    with Session(request.app.state.engine) as session:
        get_record(session, record_id)
        clip = session.scalar(select(AudioClip).where(AudioClip.id == audio_id, AudioClip.record_id == record_id))
        if clip is None:
            raise HTTPException(404, "未找到当前笔录对应的原音。")
        raw, size = clip.raw_audio, clip.size_bytes
        headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": f'{"attachment" if download else "inline"}; filename="recording.{clip.format}"'}
        requested = request.headers.get("range")
        start, end = 0, size - 1
        if requested:
            match = re.fullmatch(r"bytes=(\d{0,15})-(\d{0,15})", requested)
            if not match or not any(match.groups()):
                return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
            left, right = match.groups()
            if left:
                start, end = int(left), min(int(right), size - 1) if right else size - 1
            else:
                length = int(right)
                start, end = max(0, size - length), size - 1
                if length == 0:
                    start = size
            if start >= size or end < start:
                return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Response(raw[start:end + 1], status_code=206 if requested else 200, media_type=clip.mime_type, headers=headers)


@router.get("/{record_id}/revisions/{revision}")
def read_revision(record_id: str, revision: int, request: Request):
    with Session(request.app.state.engine) as session:
        get_record(session, record_id)
        item = session.scalar(select(RecordVersion).where(RecordVersion.record_id == record_id, RecordVersion.revision == revision))
        if item is None:
            raise HTTPException(404, "未找到该修订版本。")
        return {"revision": item.revision, "action": item.action, "created_at": timestamp(item.created_at), "snapshot": item.snapshot}


def confirmed_settings(request, body, purpose):
    if not body.confirmed:
        raise HTTPException(400, "须明确确认将本次选定原音或当前片段文本发送给配置的 AI 服务商；未发送资料。")
    settings = settings_copy(request)
    try:
        provider, model, _ = provider_config(settings, purpose)
    except RecordAIError as error:
        raise HTTPException(error.status, error.message) from None
    return settings, provider, model


def mark_failed(engine, model, identifier, message):
    with write_session(engine) as session:
        attempt = session.get(model, identifier)
        attempt.status, attempt.error, attempt.finished_at = "failed", message, utc_now()
        audit(session, "ai_failed", record_id=attempt.record_id, attempt_id=identifier)


@router.post("/{record_id}/audio/{audio_id}/transcribe")
def transcribe(record_id: str, audio_id: str, body: AIRequest, request: Request):
    settings, provider, model = confirmed_settings(request, body, "asr")
    engine = request.app.state.engine
    with write_session(engine) as session:
        row = get_record(session, record_id, True)
        mutable(row, body.expected_revision)
        clip = session.scalar(select(AudioClip).where(AudioClip.id == audio_id, AudioClip.record_id == record_id))
        if clip is None:
            raise HTTPException(404, "未找到当前笔录的所选原音。")
        if clip.duration_ms is not None and clip.duration_ms > LIMITS["asr_max_duration_ms"]:
            raise HTTPException(422, "声明时长超过服务商1小时限制，未发送原音；请在本地拆分完整音频。")
        raw, file_format, mime_type = clip.raw_audio, clip.format, clip.mime_type
        attempt = TranscriptionAttempt(record_id=row.id, audio_id=clip.id, provider=provider, model=model, input_revision=row.revision)
        session.add(attempt)
        session.flush()
        identifier = attempt.id
        audit(session, "transcription_started", record_id=row.id, attempt_id=identifier, audio_id=audio_id)
    try:
        output = transcribe_audio(raw, file_format, mime_type, settings)
    except RecordAIError as error:
        mark_failed(engine, TranscriptionAttempt, identifier, error.message)
        raise HTTPException(error.status, error.message) from None
    stale, limited = False, False
    with write_session(engine) as session:
        row = get_record(session, record_id, True)
        attempt = session.get(TranscriptionAttempt, identifier)
        attempt.text, attempt.finished_at = output["text"], utc_now()
        segments = ordered_segments(session, row.id)
        if row.revision != body.expected_revision or row.status != "draft":
            stale = True
            attempt.status, attempt.error = "stale", "请求期间笔录已变化，原始转写已留存但未加入当前正文。"
            audit(session, "transcription_stale", record_id=row.id, attempt_id=identifier)
        elif len(segments) >= LIMITS["max_segments"] or sum(len(item.text) for item in segments) + len(output["text"]) > LIMITS["max_total_text_chars"]:
            limited = True
            attempt.status, attempt.error = "failed", "正文片段数量或总长度已达到上限，原始转写已留存但未加入正文。"
            audit(session, "ai_failed", record_id=row.id, attempt_id=identifier)
        else:
            attempt.status = "succeeded"
            session.add(RecordSegment(record_id=row.id, position=len(segments), audio_id=audio_id, speaker="unknown", text=output["text"].strip(), source="asr", transcription_id=identifier))
            session.flush()
            version(session, row, "transcribed")
        session.flush()
        result = detail(session, row)
    if stale or limited:
        raise HTTPException(409 if stale else 422, "原始转写已留存，但因笔录版本变化或正文达到上限未自动加入；请重新读取并核对。")
    return result


@router.post("/{record_id}/analyze")
def analyze(record_id: str, body: AIRequest, request: Request):
    settings, provider, model = confirmed_settings(request, body, "analysis")
    engine = request.app.state.engine
    with write_session(engine) as session:
        row = get_record(session, record_id, True)
        mutable(row, body.expected_revision)
        segments = ordered_segments(session, row.id)
        inputs = input_segments(segments)
        if not inputs or sum(len(item["text"]) for item in inputs) > LIMITS["max_analyze_chars"]:
            raise HTTPException(422, "请先保存非空修订片段；单次摘要当前文本最多30000字，未发送资料。")
        attempt = RecordAnalysis(record_id=row.id, input_revision=row.revision, input_segments=inputs, input_hash=segment_hash(segments), provider=provider, model=model)
        session.add(attempt)
        session.flush()
        identifier = attempt.id
        audit(session, "analysis_started", record_id=row.id, attempt_id=identifier, segment_count=len(inputs))
    try:
        output = analyze_segments(inputs, settings)
    except RecordAIError as error:
        mark_failed(engine, RecordAnalysis, identifier, error.message)
        raise HTTPException(error.status, error.message) from None
    stale = False
    with write_session(engine) as session:
        row = get_record(session, record_id, True)
        attempt = session.get(RecordAnalysis, identifier)
        for key in ("summary", "key_facts", "issues"):
            setattr(attempt, key, output[key])
        attempt.finished_at = utc_now()
        if row.revision != body.expected_revision or row.status != "draft":
            stale = True
            attempt.status, attempt.error = "stale", "请求期间笔录已变化，结果仅存历史，未应用为当前摘要。"
            audit(session, "analysis_stale", record_id=row.id, attempt_id=identifier)
        else:
            attempt.status = "succeeded"
            version(session, row, "analyzed")
        session.flush()
        result = detail(session, row)
    if stale:
        raise HTTPException(409, "请求期间笔录版本已变化，本次摘要仅留存历史；请重新读取并核对。")
    return result


def export_record(record_id, request, format, include_ai):
    with write_session(request.app.state.engine) as session:
        row = get_record(session, record_id, True)
        if row.status == "archived":
            raise HTTPException(409, "归档笔录停止导出，可恢复到归档前状态后再导出；原音仍可回听。")
        segments = ordered_segments(session, row.id)
        if not segments:
            raise HTTPException(422, "请先保存非空修订正文再导出。")
        body = render_text(row, segments)
        if include_ai:
            analysis = session.scalar(select(RecordAnalysis).where(RecordAnalysis.record_id == row.id, RecordAnalysis.status == "succeeded").order_by(RecordAnalysis.created_at.desc(), RecordAnalysis.id))
            if analysis is None or analysis.input_hash != segment_hash(segments):
                raise HTTPException(409, "当前无有效摘要或摘要已因片段修订过期，不能附入导出。")
            body += f"\n\nAI摘要附录（辅助整理，未自动核实；不属于原始询问陈述）\n分析编号：{analysis.id}；输入版本：{analysis.input_revision}\n" + analysis.summary
            for fact in analysis.key_facts:
                body += f"\n{fact['label']}：{fact['value']} [来源片段：{', '.join(fact['segment_ids'])}]"
            if analysis.issues:
                body += "\n待核对事项：\n" + "\n".join(analysis.issues)
            body += "\n引用编号校验不代表内容经过语义或法律正确性核实。"
        label = REVIEWED_LABEL if row.status == "reviewed" else DRAFT_LABEL
        content = make_docx(row.title, body, label) if format == "docx" else (row.title + "\n" + label + "\n\n" + body).encode("utf-8-sig")
        audit(session, "exported", record_id=row.id, revision=row.revision)
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document" if format == "docx" else "text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="record.{format}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/{record_id}/export.txt")
def export_txt(record_id: str, request: Request, include_ai: bool = False):
    return export_record(record_id, request, "txt", include_ai)


@router.get("/{record_id}/export.docx")
def export_docx(record_id: str, request: Request, include_ai: bool = False):
    return export_record(record_id, request, "docx", include_ai)


def recover_interrupted_attempts(engine):
    """Single-process startup recovery. Never replay an external request."""
    counts = {"transcriptions": 0, "analyses": 0}
    with write_session(engine) as session:
        for name, model in (("transcriptions", TranscriptionAttempt), ("analyses", RecordAnalysis)):
            for attempt in session.scalars(select(model).where(model.status == "processing").with_for_update()):
                attempt.status = "failed"
                attempt.error = "上次服务中断，无法确认外部请求结果；未自动重试。请先核对已留存原音、原始转写和历史后决定是否重新发送。"
                attempt.finished_at = utc_now()
                counts[name] += 1
        if any(counts.values()):
            audit(session, "interrupted_recovered", transcription_count=counts["transcriptions"], analysis_count=counts["analyses"])
    return counts
