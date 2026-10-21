from contextlib import asynccontextmanager
import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from .ai import AIConnectionError, test_ai_connection
from .config import ConfigurationError, SettingsStore
from .database import AuditEvent, Base, application_engine, probe_postgres, write_audit
from .people import Person, PersonStay, router as people_router
from .knowledge import KnowledgeDocument, router as knowledge_router
from .alerts import Alert, AlertRule, scan_alerts, scheduler_loop, router as alerts_router
from .documents import DocumentDraft, seed_templates, router as documents_router
from .records import RecordSession, recover_interrupted_attempts, router as records_router
from .profiles import router as profiles_router
from .analytics import router as analytics_router
from .migration import router as migration_router
from .maintenance import router as maintenance_router
from .demo import router as demo_router
from .schemas import AITestRequest, PostgresUpdate, SettingsUpdate, database_identity, merge_postgres, merge_settings, public_settings

VERSION = "0.5.1"
logger = logging.getLogger("next_nia")


def dashboard_data(engine) -> dict:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    today = now.date()
    result = {"demo": False, "updated_at": now.isoformat(), "stats": {}, "trend": [], "activities": [], "alerts": [], "alerts_last_scan_at": None}
    actions = {"person.created": "新增人员档案", "person.updated": "更新人员资料", "person.archived": "归档人员档案", "person.restored": "恢复人员档案", "person.stay_created": "登记停留记录", "person.stay_updated": "更新停留记录", "people.imported": "批量导入人员资料", "knowledge.imported": "导入法规资料", "knowledge.updated": "更新法规版本信息", "knowledge.asked": "完成法规问答"}
    actions.update({"alerts.action_start": "开始预警核查", "alerts.action_close": "完成预警核查", "alerts.action_reopen": "重新开启核查", "document.created": "创建文书草稿", "document.updated": "更新文书草稿", "document.reviewed": "完成人工文书核对", "document.archived": "归档文书"})
    actions.update({"records.created": "新建询问笔录", "records.segments_updated": "保存笔录校对", "records.audio_added": "留存原始录音", "records.transcribed": "完成语音转写", "records.analyzed": "整理笔录摘要", "records.reviewed": "完成人工笔录核对"})
    with Session(engine) as session:
        result["stats"] = {
            "people": session.scalar(select(func.count()).select_from(Person).where(Person.archived.is_(False))) or 0,
            "registered_stays": session.scalar(select(func.count()).select_from(PersonStay).join(Person).where(Person.archived.is_(False))) or 0,
            "knowledge_documents": session.scalar(select(func.count()).select_from(KnowledgeDocument).where(KnowledgeDocument.status == "published")) or 0,
            "pending_reviews": session.scalar(select(func.count()).select_from(KnowledgeDocument).where(KnowledgeDocument.status == "draft")) or 0,
            "active_permits": 0,
            "pending_alerts": session.scalar(select(func.count()).select_from(Alert).join(Person).where(Person.archived.is_(False), Alert.status.in_(["open", "in_progress"]))) or 0,
            "transcript_drafts": session.scalar(select(func.count()).select_from(RecordSession).where(RecordSession.status == "draft")) or 0,
            "draft_records": session.scalar(select(func.count()).select_from(DocumentDraft).where(DocumentDraft.status == "draft")) or 0,
        }
        categories = {"due_soon": "期限临近", "due_today": "今日到期", "past_due": "登记期限已过", "needs_review": "资料待核实"}
        alert_rows = session.execute(select(Alert, Person).join(Person).where(Person.archived.is_(False), Alert.status.in_(["open", "in_progress"])).order_by(Alert.updated_at.desc()).limit(5)).all()
        result["alerts"] = [{"id": item.id, "name": person.name, "code": person.code, "category": categories[item.category], "days": item.days_remaining, "status": "核查中" if item.status == "in_progress" else "待核查"} for item, person in alert_rows]
        rule = session.get(AlertRule, 1)
        if rule and rule.last_scan_at:
            result["alerts_last_scan_at"] = (rule.last_scan_at.replace(tzinfo=timezone.utc) if rule.last_scan_at.tzinfo is None else rule.last_scan_at).isoformat()
        if result["stats"]["registered_stays"]:
            for offset in range(6, -1, -1):
                day = today - timedelta(days=offset)
                base = select(func.count()).select_from(PersonStay).join(Person).where(Person.archived.is_(False))
                result["trend"].append({"label": day.strftime("%m/%d"), "entries": session.scalar(base.where(PersonStay.entry_date == day)) or 0, "exits": session.scalar(base.where(PersonStay.exit_date == day)) or 0})
        events = session.scalars(select(AuditEvent).where(AuditEvent.action.in_(actions)).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(4)).all()
        for event in events:
            created = event.created_at.replace(tzinfo=timezone.utc) if event.created_at.tzinfo is None else event.created_at
            area = "人员档案" if event.action.startswith(("person.", "people.")) else "期限核查" if event.action.startswith("alerts.") else "文书中心" if event.action.startswith("document.") else "智能笔录" if event.action.startswith("records.") else "法规知识库"
            result["activities"].append({"id": str(event.id), "title": actions[event.action], "description": "本地操作记录 · " + area, "time": created.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%m/%d %H:%M"), "type": "person" if area == "人员档案" else "document" if area == "文书中心" else "knowledge"})
    return result


def create_app(data_dir: Path | str | None = None) -> FastAPI:
    configured_dir = data_dir or os.environ.get("NEXT_NIA_DATA_DIR") or Path(__file__).resolve().parents[1] / "data"
    directory = Path(configured_dir).expanduser().resolve()
    store = SettingsStore(directory)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        settings = store.load()
        engine = application_engine(settings, directory)
        try:
            Base.metadata.create_all(engine)
            seed_templates(engine)
            recover_interrupted_attempts(engine)
            application.state.engine = engine
            application.state.active_settings = settings.model_copy(deep=True)
            application.state.audit_available = True
            write_audit(engine, "system.started", {"environment": settings.environment.mode, "version": VERSION})
            # SQLite is a local operational file too; keep it private by default.
            if settings.environment.mode == "test":
                (directory / "test.sqlite3").chmod(0o600)
        except (SQLAlchemyError, OSError):
            engine.dispose()
            raise ConfigurationError("数据库初始化失败，请检查已保存的环境配置、数据库可用性及权限；未切换到测试环境。") from None
        try:
            await asyncio.to_thread(scan_alerts, engine, automatic=True)
        except SQLAlchemyError:
            logger.error("启动时期限扫描未完成，后台任务将重试。")
        stop_scanner = asyncio.Event()
        scanner = asyncio.create_task(scheduler_loop(application, stop_scanner))
        try:
            yield
        finally:
            stop_scanner.set()
            await scanner
            engine.dispose()

    application = FastAPI(title="境智 · 本地业务服务", version=VERSION, lifespan=lifespan, docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    application.state.store = store

    def runtime() -> dict:
        active = application.state.active_settings
        return {
            "active_environment": active.environment.mode,
            "database_engine": "SQLite" if active.environment.mode == "test" else "PostgreSQL",
            "pending_restart": database_identity(active) != database_identity(store.current),
            "audit_available": application.state.audit_available,
        }

    def audit(action: str, details: dict) -> None:
        try:
            write_audit(application.state.engine, action, details)
            application.state.audit_available = True
        except SQLAlchemyError:
            application.state.audit_available = False
            logger.error("本地审计写入失败，请检查数据库可用性。")

    def envelope() -> dict:
        return {"settings": public_settings(store.current), "runtime": runtime()}

    @application.middleware("http")
    async def private_api(request: Request, call_next):
        # A local app has no login in this phase. Block cross-site mutations rather
        # than allowing an unrelated webpage to change local configuration.
        if request.method in {"PUT", "POST", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            from urllib.parse import urlsplit
            if origin:
                parsed_origin = urlsplit(origin)
                if parsed_origin.scheme not in {"http", "https"} or parsed_origin.netloc != request.headers.get("host"):
                    return JSONResponse(status_code=403, content={"detail": "不允许跨站修改配置，请从本系统页面操作。"})
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse(status_code=403, content={"detail": "不允许跨站修改配置，请从本系统页面操作。"})
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # FastAPI's normal validation payload includes input values. Never echo it.
        return JSONResponse(status_code=422, content={"detail": "请求字段格式不正确，请检查必填内容、日期、字段长度及选项。"})

    @application.exception_handler(ConfigurationError)
    async def configuration_error(request: Request, error: ConfigurationError):
        return JSONResponse(status_code=500, content={"detail": str(error)})

    @application.get("/api/health")
    def health():
        return {"status": "ok" if application.state.audit_available else "degraded", "version": VERSION, "stage": 5, **runtime()}

    @application.get("/api/settings")
    def get_settings():
        with store.lock:
            return envelope()

    @application.put("/api/settings")
    def update_settings(update: SettingsUpdate):
        with store.lock:
            settings = merge_settings(update, store.current)
            if settings.ai.enabled:
                selected = getattr(settings.ai.providers, settings.ai.default_provider)
                if not selected.enabled:
                    raise HTTPException(422, "启用 AI 前，请先启用默认服务商。")
            if settings.environment.mode == "production":
                try:
                    probe_postgres(settings.environment.postgres)
                except (SQLAlchemyError, OSError, ValueError):
                    audit("settings.database_validation_failed", {"environment": "production"})
                    raise HTTPException(400, "正式数据库连接验证失败，设置未保存。请检查主机、端口、数据库、账号和密码。") from None
            store.save(settings)
            audit("settings.updated", {"environment": settings.environment.mode, "ai_enabled": settings.ai.enabled, "default_provider": settings.ai.default_provider, "pending_restart": runtime()["pending_restart"]})
            return envelope()

    @application.post("/api/settings/test-database")
    def test_database(update: PostgresUpdate):
        with store.lock:
            config = merge_postgres(update, store.current.environment.postgres)
        try:
            latency = probe_postgres(config)
        except (SQLAlchemyError, OSError, ValueError):
            audit("database.connection_test", {"ok": False})
            raise HTTPException(400, "数据库连接失败，请检查服务状态、网络、数据库名称与凭证。") from None
        audit("database.connection_test", {"ok": True})
        return {"ok": True, "message": "数据库连接验证通过；仅执行 SELECT 1，未建表或迁移数据。", "latency_ms": latency}

    @application.post("/api/settings/test-ai")
    def test_ai(request: AITestRequest):
        with store.lock:
            settings = store.current.model_copy(deep=True)
        try:
            result = test_ai_connection(request, settings)
        except AIConnectionError as error:
            audit("ai.connection_test", {"provider": request.provider, "ok": False})
            raise HTTPException(error.status, error.message) from None
        audit("ai.connection_test", {"provider": request.provider, "ok": True})
        return result

    @application.get("/api/dashboard")
    def get_dashboard():
        return dashboard_data(application.state.engine)

    application.include_router(people_router)
    application.include_router(knowledge_router)
    application.include_router(alerts_router)
    application.include_router(documents_router)
    application.include_router(records_router)
    application.include_router(profiles_router)
    application.include_router(analytics_router)
    application.include_router(migration_router)
    application.include_router(maintenance_router)
    application.include_router(demo_router)
    if os.environ.get('NEXT_NIA_SERVE_DIST') == '1':
        from fastapi.staticfiles import StaticFiles
        dist = Path(__file__).resolve().parents[2] / 'dist'
        if not (dist / 'index.html').is_file():
            raise ConfigurationError('未找到前端构建，请先执行 npm run build。')
        application.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
    return application


app = create_app()
