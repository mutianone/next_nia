from contextlib import asynccontextmanager
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
from .schemas import AITestRequest, PostgresUpdate, SettingsUpdate, database_identity, merge_postgres, merge_settings, public_settings

VERSION = "0.2.0"
logger = logging.getLogger("next_nia")


def dashboard_data(engine) -> dict:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    today = now.date()
    result = {"demo": False, "updated_at": now.isoformat(), "stats": {}, "trend": [], "activities": [], "alerts": []}
    actions = {"person.created": "新增人员档案", "person.updated": "更新人员资料", "person.archived": "归档人员档案", "person.restored": "恢复人员档案", "person.stay_created": "登记停留记录", "person.stay_updated": "更新停留记录", "people.imported": "批量导入人员资料", "knowledge.imported": "导入法规资料", "knowledge.updated": "更新法规版本信息", "knowledge.asked": "完成法规问答"}
    with Session(engine) as session:
        result["stats"] = {
            "people": session.scalar(select(func.count()).select_from(Person).where(Person.archived.is_(False))) or 0,
            "registered_stays": session.scalar(select(func.count()).select_from(PersonStay).join(Person).where(Person.archived.is_(False))) or 0,
            "knowledge_documents": session.scalar(select(func.count()).select_from(KnowledgeDocument).where(KnowledgeDocument.status == "published")) or 0,
            "pending_reviews": session.scalar(select(func.count()).select_from(KnowledgeDocument).where(KnowledgeDocument.status == "draft")) or 0,
            "active_permits": 0, "pending_alerts": 0, "draft_records": 0,
        }
        if result["stats"]["registered_stays"]:
            for offset in range(6, -1, -1):
                day = today - timedelta(days=offset)
                base = select(func.count()).select_from(PersonStay).join(Person).where(Person.archived.is_(False))
                result["trend"].append({"label": day.strftime("%m/%d"), "entries": session.scalar(base.where(PersonStay.entry_date == day)) or 0, "exits": session.scalar(base.where(PersonStay.exit_date == day)) or 0})
        events = session.scalars(select(AuditEvent).where(AuditEvent.action.in_(actions)).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(4)).all()
        for event in events:
            created = event.created_at.replace(tzinfo=timezone.utc) if event.created_at.tzinfo is None else event.created_at
            result["activities"].append({"id": str(event.id), "title": actions[event.action], "description": "本地操作记录 · " + ("人员档案" if event.action.startswith(("person.", "people.")) else "法规知识库"), "time": created.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%m/%d %H:%M"), "type": "person" if event.action.startswith(("person.", "people.")) else "knowledge"})
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
            yield
        finally:
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
        return {"status": "ok" if application.state.audit_available else "degraded", "version": VERSION, "stage": 2, **runtime()}

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
    return application


app = create_app()
