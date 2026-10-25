"""Local, versioned regulation library. Imported content is never executable."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
from io import BytesIO
from pathlib import PurePath
import re
from threading import RLock
from typing import Literal
from urllib.parse import quote
from uuid import uuid4
import xml.etree.ElementTree as ET
import zipfile

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from fastapi.encoders import jsonable_encoder
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column

from .database import AuditEvent, Base
from .knowledge_ai import KnowledgeAIError, answer_with_sources
from .knowledge_stream import stream_answer
from .retrieval import rank_sources, retrieval_status

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_TEXT = 1_000_000
MAX_CHUNKS = 1000
REVIEW_NOTICE = "由业务人员审核来源、版本与适用日期；发布状态不代表系统认证其官方效力。"
_WRITE_LOCK = RLock()


class BoundedUploadRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def bounded(request: Request):
            if request.url.path.endswith("/import") and request.method == "POST":
                # Bound the complete multipart body before FastAPI parses it,
                # including chunked uploads without Content-Length.
                received = bytearray()
                async for chunk in request.stream():
                    received.extend(chunk)
                    if len(received) > MAX_FILE_SIZE + 64 * 1024:
                        raise HTTPException(413, "上传请求超过10MB文件限制，请精简后重新上传。")
                async def receive():
                    return {"type": "http.request", "body": bytes(received), "more_body": False}
                request = Request(request.scope, receive)
            return await original(request)

        return bounded


router = APIRouter(prefix="/api/knowledge", tags=["法规知识库"], route_class=BoundedUploadRoute)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (UniqueConstraint("series_key", "version", name="uq_knowledge_series_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(80))
    authority: Mapped[str] = mapped_column(String(200), default="")
    doc_number: Mapped[str] = mapped_column(String(120), default="")
    category: Mapped[str] = mapped_column(String(80), default="")
    source: Mapped[str] = mapped_column(String(1000), default="")
    series_key: Mapped[str] = mapped_column(String(200), index=True)
    published_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    allow_external: Mapped[bool] = mapped_column(Boolean, default=False)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(12))
    size_bytes: Mapped[int] = mapped_column(Integer)
    raw_file: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    sha256: Mapped[str] = mapped_column(String(64))
    chunk_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class KnowledgeSection(Base):
    __tablename__ = "knowledge_sections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    article: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    parent_id: Mapped[str] = mapped_column(ForeignKey("knowledge_sections.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    article: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=80)
    authority: str = Field(default="", max_length=200)
    doc_number: str = Field(default="", max_length=120)
    category: str = Field(default="", max_length=80)
    source: str = Field(default="", max_length=1000)
    series_key: str = Field(default="", max_length=200)
    published_on: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    status: Literal["draft", "published", "archived"] = "draft"
    allow_external: bool = True

    @field_validator("title", "version", "authority", "doc_number", "category", "source", "series_key")
    @classmethod
    def clean_text(cls, value: str) -> str:
        if any(character in value for character in "\x00\r\n"):
            raise ValueError("元信息不能包含控制字符")
        return value.strip()

    @model_validator(mode="after")
    def check_metadata(self):
        if not self.title or not self.version:
            raise ValueError("标题和版本不能为空")
        self.series_key = (self.series_key or self.title).casefold()
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("失效日期不能早于生效日期")
        if self.published_on and self.effective_from and self.published_on > self.effective_from:
            raise ValueError("发布日期不能晚于生效日期，请核实资料")
        if self.status == "published" and self.effective_from is None:
            raise ValueError("发布前必须填写经核实的生效日期")
        if self.status == "published" and not self.source:
            raise ValueError("发布前必须填写经人工核实的来源说明或来源地址")
        return self


class MetadataPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    version: str | None = None
    authority: str | None = None
    doc_number: str | None = None
    category: str | None = None
    source: str | None = None
    series_key: str | None = None
    published_on: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    status: Literal["draft", "published", "archived"] | None = None
    allow_external: bool | None = None


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=1000)
    as_of: date | None = None
    limit: int = Field(default=8, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("查询不能为空")
        return value.strip()


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=1000)
    as_of: date | None = None
    confirm_external: bool = False
    template_id: str | None = Field(default=None, max_length=36)
    template_revision: int | None = Field(default=None, ge=1)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


def _today() -> date:
    # Business dates follow China Standard Time, regardless of server timezone.
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _metadata_error(error: ValidationError) -> str:
    labels = {"title": "标题", "version": "版本", "authority": "发布机关", "doc_number": "文号", "category": "分类", "source": "来源", "series_key": "法规系列标识", "published_on": "发布日期", "effective_from": "生效日期", "effective_to": "失效日期", "status": "状态", "allow_external": "外发许可"}
    messages = []
    for problem in error.errors(include_input=False):
        label = labels.get(str(problem["loc"][0]), "元信息") if problem["loc"] else "元信息"
        context = problem.get("ctx", {})
        if problem["type"] == "value_error":
            message = str(context.get("error", "元信息不正确"))
        elif problem["type"] == "string_too_long":
            message = f"{label}不能超过{context['max_length']}个字符"
        elif problem["type"] == "string_too_short":
            message = f"{label}不能为空"
        elif problem["type"].startswith("date_"):
            message = f"{label}必须是有效的YYYY-MM-DD日期"
        else:
            message = f"{label}格式不正确"
        if message not in messages:
            messages.append(message)
    return "；".join(messages[:5]) + "。"


@contextmanager
def _session(request: Request, write: bool = False):
    try:
        with Session(request.app.state.engine, expire_on_commit=False) as session:
            if write:
                # Serialize library writes in SQLite. PostgreSQL uses a DB-level
                # advisory lock too so version-overlap checks work across workers.
                if session.bind.dialect.name == "postgresql":
                    session.execute(text("SELECT pg_advisory_xact_lock(731984205)"))
                elif session.bind.dialect.name == "sqlite":
                    session.execute(text("BEGIN IMMEDIATE"))
            yield session
            if write:
                session.commit()
    except HTTPException:
        raise
    except IntegrityError:
        raise HTTPException(409, "同一法规系列的版本已存在，或数据发生冲突；未保存。") from None
    except SQLAlchemyError:
        raise HTTPException(503, "知识库数据库操作失败，请检查本地数据库状态；未确认保存。") from None


def _audit(session: Session, action: str, document_id: str | None = None, count: int = 0):
    details = {"count": count}
    if document_id:
        details["document_id"] = document_id
    session.add(AuditEvent(action=action, details=details))


def _metadata(document: KnowledgeDocument) -> dict:
    fields = {name: getattr(document, name) for name in Metadata.model_fields}
    return {**fields, "id": document.id, "filename": document.filename, "file_type": document.file_type,
            "size_bytes": document.size_bytes, "chunk_count": document.chunk_count,
            "created_at": document.created_at, "updated_at": document.updated_at, "review_notice": REVIEW_NOTICE}


def _detail(session: Session, document: KnowledgeDocument) -> dict:
    chunks = session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id).order_by(KnowledgeChunk.ordinal)).all()
    return {**_metadata(document), "chunks": [{"id": chunk.id, "article": chunk.article, "text": chunk.text, "page": chunk.page, "parent_id": chunk.parent_id, "ordinal": chunk.ordinal} for chunk in chunks]}


def _get_document(session: Session, document_id: str) -> KnowledgeDocument:
    document = session.get(KnowledgeDocument, document_id)
    if document is None:
        raise HTTPException(404, "未找到该法规文档。")
    return document


def _validate_publish(session: Session, metadata: Metadata, document_id: str | None = None):
    if metadata.status != "published":
        return
    statement = select(KnowledgeDocument).where(KnowledgeDocument.series_key == metadata.series_key, KnowledgeDocument.status == "published")
    if document_id:
        statement = statement.where(KnowledgeDocument.id != document_id)
    for existing in session.scalars(statement):
        if (metadata.effective_from or date.min) <= (existing.effective_to or date.max) and (existing.effective_from or date.min) <= (metadata.effective_to or date.max):
            raise HTTPException(409, "同一法规系列已有生效区间重叠的已发布版本。请先核实并调整旧版失效日期或归档旧版。")


def _docx_pages(raw: bytes) -> list[tuple[str, int | None]]:
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 30 * 1024 * 1024:
                raise HTTPException(400, "DOCX解压规模超出限制，请精简后导入。")
            for entry in entries:
                if entry.flag_bits & 1 or (entry.file_size > 1024 * 1024 and entry.file_size > max(entry.compress_size, 1) * 200):
                    raise HTTPException(400, "DOCX包含加密内容或异常压缩数据，已拒绝导入。")
            if "word/document.xml" not in archive.namelist():
                raise HTTPException(400, "文件不是有效的DOCX文档。")
            xml = archive.read("word/document.xml")
            if len(xml) > 20 * 1024 * 1024 or b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                raise HTTPException(400, "DOCX结构不受支持或解压规模超出限制。")
            root = ET.fromstring(xml)
            namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            paragraphs = ["".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)) for paragraph in root.findall(".//w:p", namespace)]
            return [("\n".join(paragraphs), None)]
    except HTTPException:
        raise
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError, RuntimeError):
        raise HTTPException(400, "DOCX文件损坏或格式不受支持，未导入。") from None


def _pdf_pages(raw: bytes) -> list[tuple[str, int | None]]:
    from pypdf import apply_configuration
    with apply_configuration(
        maximum_declared_stream_length=10 * 1024 * 1024,
        array_based_stream_maximum_output_length=8 * 1024 * 1024,
        zlib_maximum_output_length=8 * 1024 * 1024,
        lzw_maximum_output_length=8 * 1024 * 1024,
        run_length_maximum_output_length=8 * 1024 * 1024,
        brotli_maximum_output_length=8 * 1024 * 1024,
        stream_decoding_work_maximum_length=20 * 1024 * 1024,
        page_tree_maximum_entries=1000,
        stream_parsing_work_maximum_count=200_000,
        jbig2dec_binary=None,
    ):
        return _read_pdf_pages(raw)


def _read_pdf_pages(raw: bytes) -> list[tuple[str, int | None]]:
    from pypdf import PdfReader
    try:
        reader = PdfReader(BytesIO(raw), strict=True)
        if reader.is_encrypted:
            raise HTTPException(400, "暂不支持加密PDF，请先提供未加密的文本PDF。")
        if len(reader.pages) > 500:
            raise HTTPException(400, "PDF超过500页，请拆分后导入。")
        pages = []
        total_stream = 0
        total_text = 0
        for number, page in enumerate(reader.pages, 1):
            content = page.get_contents()
            if content is not None:
                decoded = content.get_data()
                total_stream += len(decoded)
                if len(decoded) > 4 * 1024 * 1024 or total_stream > 20 * 1024 * 1024:
                    raise HTTPException(400, "PDF解压内容超出限制，请精简后导入。")
            extracted = page.extract_text() or ""
            total_text += len(extracted)
            if total_text > MAX_TEXT:
                raise HTTPException(400, "提取文字超过100万字符，请拆分后导入。")
            if not extracted.strip():
                resources = page.get("/Resources", {})
                if hasattr(resources, "get_object"):
                    resources = resources.get_object()
                objects = resources.get("/XObject", {})
                if hasattr(objects, "get_object"):
                    objects = objects.get_object()
                if any(value.get_object().get("/Subtype") == "/Image" for value in objects.values()):
                    raise HTTPException(400, f"PDF第{number}页为无文本扫描页，请先完成本地OCR后重新导入；本次未保存。")
            pages.append((extracted, number))
        return pages
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "PDF损坏、结构不受支持或无法安全提取文字，未导入。") from None


def _extract(raw: bytes, extension: str) -> list[tuple[str, int | None]]:
    if extension in {"txt", "md"}:
        try:
            decoded = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                decoded = raw.decode("gb18030")
            except UnicodeDecodeError:
                raise HTTPException(400, "文本编码无法识别，请转换为UTF-8后导入。") from None
        if "\x00" in decoded:
            raise HTTPException(400, "文件包含二进制内容，请提供纯文本文件。")
        pages = [(decoded, None)]
    elif extension == "docx":
        pages = _docx_pages(raw)
    elif extension == "pdf":
        if not raw.startswith(b"%PDF-"):
            raise HTTPException(400, "文件不是有效的PDF。")
        pages = _pdf_pages(raw)
    else:
        raise HTTPException(415, "支持TXT、MD、文本PDF与DOCX，暂不支持该文件类型。")
    if sum(len(content) for content, _ in pages) > MAX_TEXT:
        raise HTTPException(400, "提取文字超过100万字符，请拆分后导入。")
    if not any(content.strip() for content, _ in pages):
        raise HTTPException(400, "没有提取到文字。扫描PDF需先完成本地OCR；本次未保存。")
    return pages


def _build_chunks(document_id: str, pages: list[tuple[str, int | None]]):
    sections: list[KnowledgeSection] = []
    chunks: list[KnowledgeChunk] = []
    blocks: list[tuple[str, int | None]] = []
    heading = "正文"
    article_pattern = re.compile(r"^(?:#{1,6}\s*)?(第[一二三四五六七八九十百千万零〇两0-9]+条(?:之[一二三四五六七八九十0-9]+)?)")

    def flush():
        if not blocks:
            return
        parent_id = str(uuid4())
        sections.append(KnowledgeSection(id=parent_id, document_id=document_id, article=heading, text="\n".join(block for block, _ in blocks)))
        # Keep page boundaries so a source link never points at a fabricated page.
        grouped: list[tuple[str, int | None]] = []
        for block, page in blocks:
            if grouped and grouped[-1][1] == page:
                grouped[-1] = (grouped[-1][0] + "\n" + block, page)
            else:
                grouped.append((block, page))
        for block, page in grouped:
            start = 0
            while start < len(block):
                end = min(start + 900, len(block))
                piece = block[start:end].strip()
                if piece:
                    chunks.append(KnowledgeChunk(id=str(uuid4()), document_id=document_id, parent_id=parent_id, ordinal=len(chunks) + 1, article=heading, text=piece, page=page))
                if len(chunks) > MAX_CHUNKS:
                    raise HTTPException(400, "文档切片超过1000段，请拆分法规文件后导入。")
                if end == len(block):
                    break
                start = end - 100
        blocks.clear()

    for content, page in pages:
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = article_pattern.match(line)
            if match:
                flush()
                heading = match.group(1)
            blocks.append((line, page))
    flush()
    if not chunks:
        raise HTTPException(400, "文件没有可用文字内容，未导入。")
    return sections, chunks


def _search(session: Session, query: str, as_of: date, limit: int, external_only: bool = False):
    statement = select(KnowledgeChunk, KnowledgeDocument).join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id).where(
        KnowledgeDocument.status == "published", KnowledgeDocument.effective_from <= as_of,
        or_(KnowledgeDocument.effective_to.is_(None), KnowledgeDocument.effective_to >= as_of))
    if external_only:
        statement = statement.where(KnowledgeDocument.allow_external.is_(True))
    sources = []
    for chunk, document in session.execute(statement):
        sources.append({"id": chunk.id, "document_id": document.id, "title": document.title, "version": document.version,
                        "article": chunk.article, "text": chunk.text, "page": chunk.page, "source": document.source,
                        "effective_from": document.effective_from.isoformat() if document.effective_from else None,
                        "effective_to": document.effective_to.isoformat() if document.effective_to else None,
                        "allow_external": document.allow_external})
    return rank_sources(query, sources, limit)


@router.get("")
def list_documents(request: Request, q: str = Query(default="", max_length=200), status: Literal["draft", "published", "archived"] | None = None, page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100)):
    with _session(request) as session:
        criteria = []
        if q.strip():
            escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            criteria.append(or_(KnowledgeDocument.title.ilike(f"%{escaped}%", escape="\\"), KnowledgeDocument.doc_number.ilike(f"%{escaped}%", escape="\\"), KnowledgeDocument.version.ilike(f"%{escaped}%", escape="\\")))
        if status:
            criteria.append(KnowledgeDocument.status == status)
        total = session.scalar(select(func.count()).select_from(KnowledgeDocument).where(*criteria))
        documents = session.scalars(select(KnowledgeDocument).where(*criteria).order_by(KnowledgeDocument.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
        statuses = dict(session.execute(select(KnowledgeDocument.status, func.count()).group_by(KnowledgeDocument.status)).all())
        stats = {"documents": sum(statuses.values()), "published": statuses.get("published", 0), "drafts": statuses.get("draft", 0), "chunks": session.scalar(select(func.count()).select_from(KnowledgeChunk))}
        return {"items": [_metadata(document) for document in documents], "total": total, "page": page, "page_size": page_size, "stats": stats, "retrieval": retrieval_status()}


@router.post("/import", status_code=201)
def import_document(request: Request, file: UploadFile = File(...), title: str = Form(...), version: str = Form(...), authority: str = Form(""), doc_number: str = Form(""), category: str = Form(""), source: str = Form(""), series_key: str = Form(""), published_on: str = Form(""), effective_from: str = Form(""), effective_to: str = Form("")):
    try:
        metadata = Metadata(title=title, version=version, authority=authority, doc_number=doc_number, category=category, source=source, series_key=series_key,
                            published_on=published_on or None, effective_from=effective_from or None, effective_to=effective_to or None)
    except ValidationError as error:
        raise HTTPException(422, _metadata_error(error)) from None
    raw = file.file.read(MAX_FILE_SIZE + 1)
    if not raw or len(raw) > MAX_FILE_SIZE:
        raise HTTPException(413, "文件为空或超过10MB，请检查后重新上传。")
    filename = (file.filename or "document.txt").replace("\\", "/").rsplit("/", 1)[-1]
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)[:255] or "document.txt"
    extension = PurePath(filename).suffix.lower().lstrip(".")
    pages = _extract(raw, extension)
    document_id = str(uuid4())
    sections, chunks = _build_chunks(document_id, pages)
    with _WRITE_LOCK, _session(request, write=True) as session:
        document = KnowledgeDocument(id=document_id, **metadata.model_dump(), filename=filename, file_type=extension, size_bytes=len(raw), raw_file=raw,
                                     sha256=hashlib.sha256(raw).hexdigest(), chunk_count=len(chunks))
        session.add(document)
        session.flush()
        session.add_all(sections)
        session.flush()
        session.add_all(chunks)
        _audit(session, "knowledge.imported", document_id, len(chunks))
        session.flush()
        result = _detail(session, document)
    return result


@router.post("/search")
def search(request: Request, body: SearchRequest):
    as_of = body.as_of or _today()
    with _session(request) as session:
        items, retrieval = _search(session, body.query, as_of, body.limit)
    return {"items": items, "retrieval": retrieval, "as_of": as_of}


@router.post("/ask")
def ask(request: Request, body: AskRequest):
    as_of = body.as_of or _today()
    question = body.question
    template_sources = None
    if body.template_id:
        from .analysis_templates import resolve_template, analysis_question
        with _WRITE_LOCK, _session(request) as session:
            template, template_sources = resolve_template(session, body.template_id, body.template_revision, as_of)
        question = analysis_question(body.question, template, as_of)
    with request.app.state.store.lock:
        settings = request.app.state.store.current.model_copy(deep=True)
    if not settings.ai.external_permissions.question:
        raise HTTPException(403, "请前往系统配置 → AI 服务，勾选允许法规问答发送至服务商；非管理员请联系管理员。")
    # Keep publication/external-permission checks and the outbound request in one
    # serialized operation. A concurrent revocation cannot slip between them.
    with _WRITE_LOCK, _session(request, write=True) as session:
        sources, retrieval = _search(session, body.question, as_of, 8, external_only=True)
        if template_sources is not None:
            sources = template_sources + sources[:4]
        if not sources:
            return {"status": "insufficient", "answer": "没有检索到已发布、有效且允许用于外部 AI 的相关法规。请补充材料、调整问题，或前往系统配置 → AI 服务检查法规资料许可。", "sources": [], "provider": None, "model": None, "claims": [], "as_of": as_of, "retrieval": retrieval}
        if not body.confirm_external:
            raise HTTPException(400, "需要明确确认将本次问题与检索到的公开法规片段发送给已配置的 AI 服务商。")
        try:
            result = answer_with_sources(question, sources, settings, **({"analysis": True} if template_sources is not None else {}))
        except KnowledgeAIError as error:
            raise HTTPException(error.status, error.message) from None
        _audit(session, "knowledge.asked", count=len(sources))
    return {**result, "as_of": as_of, "retrieval": retrieval}


@router.post("/ask/stream")
def ask_stream(request: Request, body: AskRequest):
    as_of = body.as_of or _today()
    question = body.question
    template_sources = None
    if body.template_id:
        from .analysis_templates import resolve_template, analysis_question
        with _WRITE_LOCK, _session(request) as session:
            template, template_sources = resolve_template(session, body.template_id, body.template_revision, as_of)
        question = analysis_question(body.question, template, as_of)
    with request.app.state.store.lock:
        settings = request.app.state.store.current.model_copy(deep=True)
    if not settings.ai.external_permissions.question:
        raise HTTPException(403, "请前往系统配置 → AI 服务，勾选允许法规问答发送至 AI。")
    if not body.confirm_external:
        raise HTTPException(400, "AI 外发许可尚未确认。")
    def events():
        def event(value):
            return "data: " + json.dumps(jsonable_encoder(value), ensure_ascii=False) + "\n\n"
        yield event({"type": "status", "message": "正在检索法规依据…"})
        # Snapshot sources under the same publication lock as non-stream requests.
        # Release it before yielding: generator resumes may use different threads.
        with _WRITE_LOCK, _session(request) as session:
            sources, retrieval = _search(session, body.question, as_of, 8, external_only=True)
        if template_sources is not None:
            sources = template_sources + sources[:4]
        if not sources:
            yield event({"type": "complete", "answer": {"status": "insufficient", "answer": "没有检索到可用于 AI 的有效法规，请补充资料或在系统配置中检查法规资料许可。", "sources": [], "claims": [], "provider": None, "model": None, "as_of": as_of, "retrieval": retrieval}})
            return
        yield event({"type": "status", "message": "已找到法规依据，AI 正在生成回答…"})
        try:
            for result in stream_answer(question, sources, settings, **({"analysis": True} if template_sources is not None else {})):
                result['answer'].update({'as_of': as_of, 'retrieval': retrieval})
                if result['type'] == 'complete':
                    with _session(request, write=True) as session:
                        _audit(session, "knowledge.asked", count=len(sources))
                yield event(result)
        except KnowledgeAIError as error:
            yield event({"type": "error", "message": error.message})
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/{document_id}")
def document_detail(request: Request, document_id: str):
    with _session(request) as session:
        return _detail(session, _get_document(session, document_id))


@router.patch("/{document_id}")
def update_document(request: Request, document_id: str, body: MetadataPatch):
    with _WRITE_LOCK, _session(request, write=True) as session:
        document = _get_document(session, document_id)
        data = {name: getattr(document, name) for name in Metadata.model_fields}
        patch = body.model_dump(exclude_unset=True)
        for name in ("authority", "doc_number", "category", "source", "series_key"):
            if name in patch and patch[name] is None:
                patch[name] = ""
        data.update(patch)
        try:
            metadata = Metadata.model_validate(data)
        except ValidationError as error:
            raise HTTPException(422, _metadata_error(error)) from None
        _validate_publish(session, metadata, document_id)
        for name, value in metadata.model_dump().items():
            setattr(document, name, value)
        document.updated_at = datetime.now(timezone.utc)
        _audit(session, "knowledge.updated", document_id, document.chunk_count)
        session.flush()
        result = _detail(session, document)
    return result


@router.get("/{document_id}/file")
def download_document(request: Request, document_id: str):
    with _session(request) as session:
        document = _get_document(session, document_id)
        raw, filename = document.raw_file, document.filename
    return Response(raw, media_type="application/octet-stream", headers={"Content-Disposition": f"attachment; filename=regulation; filename*=UTF-8''{quote(filename, safe='')}", "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
