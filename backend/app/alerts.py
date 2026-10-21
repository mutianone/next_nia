"""Deterministic local deadline reminders; no automatic immigration-law finding."""

import asyncio
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column

from .database import AuditEvent, Base
from .people import Person, PersonStay

logger = logging.getLogger("next_nia.alerts")
LOCK_ID = 731984206
SCAN_FAILURE = "自动扫描未完成，请检查数据库可用性；上次成功扫描时间未变更。"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def business_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    upcoming_days: Mapped[int] = mapped_column(Integer, default=30)
    urgent_days: Mapped[int] = mapped_column(Integer, default=7)
    scan_interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)


class StayFacts(Base):
    __tablename__ = "stay_alert_facts"
    stay_id: Mapped[str] = mapped_column(ForeignKey("person_stays.id"), primary_key=True)
    visa_valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    extension_status: Mapped[str] = mapped_column(String(12), default="none")
    extension_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    receipt_valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(500), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), index=True)
    stay_id: Mapped[str] = mapped_column(ForeignKey("person_stays.id"), unique=True)
    category: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    priority: Mapped[str] = mapped_column(String(12), default="normal")
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    days_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visa_valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    source_summary: Mapped[str] = mapped_column(Text, default="")
    fingerprint: Mapped[str] = mapped_column(String(64))
    auto_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AlertHistory(Base):
    __tablename__ = "alert_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id"), index=True)
    action: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RulesInput(StrictInput):
    enabled: bool = True
    upcoming_days: int = Field(default=30, ge=0, le=365)
    urgent_days: int = Field(default=7, ge=0, le=365)
    scan_interval_minutes: int = Field(default=60, ge=1, le=10080)

    @model_validator(mode="after")
    def validate_window(self):
        if self.urgent_days > self.upcoming_days:
            raise ValueError("紧急提醒天数不能大于提前提醒天数")
        return self


class FactsInput(StrictInput):
    visa_valid_until: date | None = None
    extension_status: Literal["none", "pending", "approved", "rejected"] = "none"
    extension_until: date | None = None
    receipt_valid_until: date | None = None
    verified: bool = False
    source: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=5000)

    @field_validator("visa_valid_until", "extension_until", "receipt_valid_until", mode="before")
    @classmethod
    def empty_date(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("source", "notes")
    @classmethod
    def valid_text(cls, value: str):
        if "\x00" in value:
            raise ValueError("文本不能包含空字符")
        return value

    @model_validator(mode="after")
    def verified_source(self):
        if self.verified and not self.source:
            raise ValueError("核实补充事实前必须填写资料来源")
        if self.verified and self.extension_status == "approved" and self.extension_until is None:
            raise ValueError("已核实的批准延期必须填写延期截止日期")
        return self


class ActionInput(StrictInput):
    action: Literal["start", "close", "reopen", "note"]
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("note")
    @classmethod
    def valid_note(cls, value: str):
        if "\x00" in value:
            raise ValueError("文本不能包含空字符")
        return value


CUSTOM_ERRORS = {"紧急提醒天数不能大于提前提醒天数", "核实补充事实前必须填写资料来源", "已核实的批准延期必须填写延期截止日期", "文本不能包含空字符"}
LABELS = {"enabled": "自动扫描开关", "upcoming_days": "提前提醒天数", "urgent_days": "紧急提醒天数", "scan_interval_minutes": "扫描间隔", "visa_valid_until": "签证有效期", "extension_status": "延期状态", "extension_until": "延期截止日期", "receipt_valid_until": "回执有效期", "verified": "核验状态", "source": "资料来源", "notes": "补充备注", "action": "处理动作", "note": "处理说明", "page": "页码", "page_size": "分页大小", "q": "搜索词", "category": "预警类别", "status": "预警状态"}


class AlertsRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as error:
                messages = []
                for item in error.errors()[:5]:
                    custom = str(item.get("ctx", {}).get("error", ""))
                    field = next((part for part in reversed(item.get("loc", ())) if part in LABELS), None)
                    message = custom if custom in CUSTOM_ERRORS else f"{LABELS.get(field, '请求')}格式、长度或取值不正确，请检查。"
                    if message not in messages:
                        messages.append(message)
                raise HTTPException(422, "；".join(messages)) from None
            except IntegrityError:
                raise HTTPException(409, "预警记录发生冲突，请刷新后重试；本次修改未保存。") from None
            except SQLAlchemyError:
                raise HTTPException(503, "预警数据库操作失败，请检查服务后重试；本次未完成的修改已撤销。") from None
        return safe


router = APIRouter(prefix="/api/alerts", tags=["期限提醒与人工核查"], route_class=AlertsRoute)


@contextmanager
def locked_session(engine):
    with Session(engine, expire_on_commit=False) as session:
        try:
            if engine.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            elif engine.dialect.name == "postgresql":
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_ID})
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


def audit(session: Session, action: str, **identifiers_or_counts):
    session.add(AuditEvent(action=action, details=identifiers_or_counts))


def rule_for_write(session: Session) -> AlertRule:
    rule = session.get(AlertRule, 1)
    if rule is None:
        rule = AlertRule(id=1, **RulesInput().model_dump())
        session.add(rule)
        session.flush()
        audit(session, "alerts.rules_initialized", count=1)
    return rule


def rule_values(rule: AlertRule | None, fallback_error: str | None = None) -> dict:
    values = RulesInput().model_dump() if rule is None else {key: getattr(rule, key) for key in RulesInput.model_fields}
    last_scan = aware(rule.last_scan_at) if rule and rule.last_scan_at else None
    next_scan = (last_scan + timedelta(minutes=values["scan_interval_minutes"]) if last_scan else utc_now()) if values["enabled"] else None
    return {**values, "last_scan_at": last_scan.isoformat() if last_scan else None,
            "next_scan_at": next_scan.isoformat() if next_scan else None,
            "last_error": (rule.last_error if rule else None) or fallback_error}


def facts_values(facts: StayFacts | None) -> dict:
    return FactsInput().model_dump() if facts is None else {key: getattr(facts, key) for key in FactsInput.model_fields}


def alert_values(alert: Alert, person: Person) -> dict:
    return {"id": alert.id, "person_id": alert.person_id, "person_name": person.name, "person_code": person.code,
            "stay_id": alert.stay_id, "category": alert.category, "status": alert.status, "priority": alert.priority,
            "deadline": alert.deadline, "days_remaining": alert.days_remaining, "visa_valid_until": alert.visa_valid_until,
            "reason": alert.reason, "source_summary": alert.source_summary, "auto_closed": alert.auto_closed,
            "last_seen_at": aware(alert.last_seen_at).isoformat(), "created_at": aware(alert.created_at).isoformat(), "updated_at": aware(alert.updated_at).isoformat()}


def history(session: Session, alert: Alert, action: str, note: str, now: datetime):
    session.add(AlertHistory(alert_id=alert.id, action=action, note=note, created_at=now))


def get_alert(session: Session, alert_id: str) -> tuple[Alert, Person]:
    result = session.execute(select(Alert, Person).join(Person, Person.id == Alert.person_id).where(Alert.id == alert_id)).first()
    if result is None:
        raise HTTPException(404, "未找到该预警记录。")
    return result[0], result[1]


def alert_detail(session: Session, alert: Alert, person: Person):
    items = session.scalars(select(AlertHistory).where(AlertHistory.alert_id == alert.id).order_by(AlertHistory.created_at, AlertHistory.id)).all()
    return {**alert_values(alert, person), "history": [{"id": item.id, "action": item.action, "note": item.note, "created_at": aware(item.created_at).isoformat()} for item in items]}


def exclusion_reason(person: Person, stay: PersonStay, today: date) -> str | None:
    if person.archived:
        return "人员档案已归档，停止当前期限提醒；不代表作出法律结论。"
    if stay.entry_date > today:
        return "登记入境日期尚未到达，不作为当前已入境记录提醒。"
    if stay.exit_date and stay.exit_date <= today:
        return "已登记离境日期且日期已到达，停止本次停留提醒。"
    return None


def assess(person: Person, stay: PersonStay, facts: StayFacts | None, peers: list[str], rule: AlertRule, today: date) -> dict | None:
    if exclusion_reason(person, stay, today):
        return None
    pending = []
    deadline = stay.permitted_until
    if person.verification != "verified":
        pending.append("人员档案尚未完成人工核验")
    if len(peers) > 1:
        pending.append("同一人员存在多条同时未离境记录，需先核实对应停留事实")
    if stay.exit_date and stay.exit_date > today:
        pending.append("离境日期在未来，不能视为已实际离境")
    if stay.permitted_until and stay.permitted_until < stay.entry_date:
        pending.append("准许停留截止日期早于当前入境日期，需核对原始记录")
    if facts:
        if not facts.verified:
            pending.append("补充事实尚未核实")
        elif not facts.source.strip():
            pending.append("补充事实缺少核实来源")
        # Original stay records can be edited after supplements are saved. Recheck
        # relationships on every scan instead of trusting the earlier PUT check.
        extension_conflict = bool(facts.extension_until and (facts.extension_until < stay.entry_date or
            (facts.extension_status == "approved" and stay.permitted_until and facts.extension_until < stay.permitted_until)))
        if extension_conflict:
            pending.append("延期截止日期与当前入境或准许停留记录矛盾，需重新核实")
        if facts.receipt_valid_until and facts.receipt_valid_until < stay.entry_date:
            pending.append("回执有效期早于当前入境日期，需重新核实")
        if facts.extension_status == "pending":
            pending.append("延期申请仍待处理，不能直接以申请或回执替代准许停留期限")
        elif facts.extension_status == "approved":
            if facts.verified and facts.extension_until and facts.source.strip() and not extension_conflict:
                deadline = facts.extension_until
            else:
                pending.append("批准延期的截止日期或来源尚待核实")
        elif facts.extension_until:
            pending.append("延期截止日期与延期状态不一致，需核实")
        if facts.receipt_valid_until and facts.extension_status != "approved":
            pending.append("已登记受理回执，回执有效期不等于准许停留截止日期")
    if deadline is None:
        pending.append("准许停留截止日期未知，不能根据签证有效期推断")
    if pending:
        category, priority = "needs_review", "review"
        # An unresolved application can change the applicable deadline. Avoid a
        # misleading countdown from the unconfirmed baseline in this category.
        display_deadline, days = None, None
        reason = "；".join(pending) + "。仅作资料核查提醒。"
    else:
        days = (deadline - today).days
        display_deadline = deadline
        if days > rule.upcoming_days:
            return None
        if days < 0:
            category, priority = "past_due", "urgent"
            reason = "登记的准许停留截止日期已过，需核查延期、离境及其他相关事实；不自动认定非法滞留。"
        elif days == 0:
            category, priority = "due_today", "urgent"
            reason = "登记的准许停留期限今日到期，今日不按逾期处理；请核实后续安排。"
        else:
            category = "due_soon"
            priority = "urgent" if days <= rule.urgent_days else "normal"
            reason = "登记的准许停留期限接近，请核实资料与后续安排。"
    evidence = {"entry_date": stay.entry_date, "permitted_until": stay.permitted_until, "exit_date": stay.exit_date,
                "verification": person.verification, "peers": sorted(peers),
                "facts": None if facts is None else {key: getattr(facts, key) for key in ("visa_valid_until", "extension_status", "extension_until", "receipt_valid_until", "verified")},
                "category": category, "deadline": deadline, "pending": pending}
    fingerprint = hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    sources = [f"人员资料：{person.source}", f"停留记录：{stay.source}"]
    if facts and facts.source:
        sources.append(f"补充事实：{facts.source}")
    return {"category": category, "priority": priority, "deadline": display_deadline, "days_remaining": days,
            "visa_valid_until": facts.visa_valid_until if facts else None, "reason": reason,
            "source_summary": "；".join(sources), "fingerprint": fingerprint}


def scan_alerts(engine, today: date | None = None, automatic: bool = False, now: datetime | None = None) -> dict | None:
    """Scan one active database. Internal date override is only for deterministic tests."""
    now = aware(now or utc_now())
    today = today or now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    with locked_session(engine) as session:
        rule = rule_for_write(session)
        if automatic and (not rule.enabled or (rule.last_scan_at and now < aware(rule.last_scan_at) + timedelta(minutes=rule.scan_interval_minutes))):
            return None
        records = session.execute(select(PersonStay, Person).join(Person, Person.id == PersonStay.person_id)).all()
        supplements = {facts.stay_id: facts for facts in session.scalars(select(StayFacts)).all()}
        existing = {item.stay_id: item for item in session.scalars(select(Alert)).all()}
        peers = defaultdict(list)
        for stay, person in records:
            if not exclusion_reason(person, stay, today):
                peers[person.id].append(stay.id)
        result = {"created": 0, "updated": 0, "closed": 0, "scanned": len(records), "scanned_at": now.isoformat()}
        for stay, person in records:
            candidate = assess(person, stay, supplements.get(stay.id), peers[person.id], rule, today)
            previous = existing.get(stay.id)
            if candidate is None:
                if previous and (previous.status != "closed" or not previous.auto_closed):
                    note = exclusion_reason(person, stay, today) or "登记期限已超出当前提前提醒窗口，当前提醒条件已消失。"
                    was_closed = previous.status == "closed"
                    previous.status, previous.auto_closed, previous.updated_at = "closed", True, now
                    previous.reason = note
                    # A manual closure suppresses only the same continuously
                    # present condition. Remember an intervening disappearance
                    # so that restoring identical facts still starts a new review.
                    history(session, previous, "auto_close", ("此前已人工关闭；" if was_closed else "") + note, now)
                    result["updated" if was_closed else "closed"] += 1
                continue
            if previous is None:
                previous = Alert(id=str(uuid4()), person_id=person.id, stay_id=stay.id, status="open", auto_closed=False,
                                 last_seen_at=now, created_at=now, updated_at=now, **candidate)
                session.add(previous)
                session.flush()
                history(session, previous, "created", candidate["reason"], now)
                result["created"] += 1
                continue
            material = previous.fingerprint != candidate["fingerprint"]
            reopen = previous.status == "closed" and (previous.auto_closed or material)
            changed = any(getattr(previous, key) != value for key, value in candidate.items()) or reopen
            for key, value in candidate.items():
                setattr(previous, key, value)
            previous.last_seen_at = now
            if reopen:
                previous.status, previous.auto_closed = "open", False
                history(session, previous, "auto_reopen", "提醒条件再次出现或关键事实、类别、期限发生变化，重新进入待核查。", now)
            elif material:
                history(session, previous, "facts_changed", "关键事实、提醒类别或适用期限发生变化，请重新核查。", now)
            if changed:
                previous.updated_at = now
                result["updated"] += 1
        rule.last_scan_at, rule.last_error = now, None
        audit(session, "alerts.scanned", created_count=result["created"], updated_count=result["updated"], closed_count=result["closed"], scanned_count=result["scanned"])
        session.flush()
        return result


def record_scheduler_error(engine):
    try:
        with locked_session(engine) as session:
            rule = rule_for_write(session)
            rule.last_error = SCAN_FAILURE
            audit(session, "alerts.scan_failed", count=1)
    except Exception:
        # DB unavailable: the application state still exposes the failure when
        # the rules endpoint next succeeds. Do not log DB diagnostics or inputs.
        pass


async def scheduler_loop(application, stop_event: asyncio.Event):
    """Polling interval is bounded; shutdown wakes immediately through the event."""
    while not stop_event.is_set():
        try:
            result = await asyncio.to_thread(scan_alerts, application.state.engine, automatic=True)
            if result is not None:
                application.state.alerts_last_error = None
        except asyncio.CancelledError:
            raise
        except Exception:
            application.state.alerts_last_error = SCAN_FAILURE
            logger.error(SCAN_FAILURE)
            await asyncio.to_thread(record_scheduler_error, application.state.engine)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass


@router.get("/rules")
def get_rules(request: Request):
    with Session(request.app.state.engine) as session:
        return rule_values(session.get(AlertRule, 1), getattr(request.app.state, "alerts_last_error", None))


@router.put("/rules")
def update_rules(data: RulesInput, request: Request):
    with locked_session(request.app.state.engine) as session:
        rule = rule_for_write(session)
        for key, value in data.model_dump().items():
            setattr(rule, key, value)
        audit(session, "alerts.rules_updated", count=1)
        session.flush()
        return rule_values(rule, getattr(request.app.state, "alerts_last_error", None))


@router.post("/scan")
async def manual_scan(request: Request):
    if await request.body():
        raise HTTPException(422, "手动扫描不接受日期或请求体，始终按当前北京时间运行。")
    result = await asyncio.to_thread(scan_alerts, request.app.state.engine)
    request.app.state.alerts_last_error = None
    return result


@router.get("/stays/{stay_id}/facts")
def get_facts(stay_id: str, request: Request):
    with Session(request.app.state.engine) as session:
        if session.get(PersonStay, stay_id) is None:
            raise HTTPException(404, "未找到该停留记录。")
        return facts_values(session.get(StayFacts, stay_id))


@router.put("/stays/{stay_id}/facts")
def update_facts(stay_id: str, data: FactsInput, request: Request):
    with locked_session(request.app.state.engine) as session:
        stay = session.get(PersonStay, stay_id)
        if stay is None:
            raise HTTPException(404, "未找到该停留记录。")
        if data.extension_until and data.extension_until < stay.entry_date:
            raise HTTPException(422, "延期截止日期不能早于入境日期。")
        if data.receipt_valid_until and data.receipt_valid_until < stay.entry_date:
            raise HTTPException(422, "回执有效期不能早于入境日期。")
        if data.verified and data.extension_status == "approved" and stay.permitted_until and data.extension_until < stay.permitted_until:
            raise HTTPException(422, "批准的延期截止日期不能早于已登记的准许停留截止日期，请核对资料。")
        facts = session.get(StayFacts, stay_id)
        if facts is None:
            facts = StayFacts(stay_id=stay_id)
            session.add(facts)
        for key, value in data.model_dump().items():
            setattr(facts, key, value)
        facts.updated_at = utc_now()
        audit(session, "alerts.facts_updated", stay_id=stay_id)
        session.flush()
        return facts_values(facts)


@router.get("")
def list_alerts(request: Request, q: str = Query(default="", max_length=200),
                status: Literal["active", "open", "in_progress", "closed", "all"] = "active",
                category: Literal["all", "due_soon", "due_today", "past_due", "needs_review"] = "all",
                page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100)):
    conditions = []
    if status == "active":
        conditions.append(Alert.status.in_(["open", "in_progress"]))
    elif status != "all":
        conditions.append(Alert.status == status)
    if category != "all":
        conditions.append(Alert.category == category)
    if q.strip():
        conditions.append(or_(Person.name.icontains(q.strip(), autoescape=True), Person.code.icontains(q.strip(), autoescape=True)))
    with Session(request.app.state.engine) as session:
        statement = select(Alert, Person).join(Person, Person.id == Alert.person_id).where(*conditions)
        total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = session.execute(statement.order_by(Alert.updated_at.desc(), Alert.id).offset((page - 1) * page_size).limit(page_size)).all()
        counts = dict(session.execute(select(Alert.category, func.count()).where(Alert.status.in_(["open", "in_progress"])).group_by(Alert.category)).all())
        stats = {name: counts.get(name, 0) for name in ("due_soon", "due_today", "past_due", "needs_review")}
        stats["active"] = sum(stats.values())
        rule = session.get(AlertRule, 1)
        return {"items": [alert_values(alert, person) for alert, person in rows], "total": total, "page": page, "page_size": page_size,
                "stats": stats, "last_scan_at": aware(rule.last_scan_at).isoformat() if rule and rule.last_scan_at else None}


@router.get("/{alert_id}")
def get_detail(alert_id: str, request: Request):
    with Session(request.app.state.engine) as session:
        alert, person = get_alert(session, alert_id)
        return alert_detail(session, alert, person)


@router.post("/{alert_id}/actions")
def process_alert(alert_id: str, data: ActionInput, request: Request):
    with locked_session(request.app.state.engine) as session:
        alert, person = get_alert(session, alert_id)
        if data.action == "start":
            if alert.status != "open":
                raise HTTPException(409, "仅待核查记录可开始处理；已关闭记录请先重新开启。")
            alert.status = "in_progress"
        elif data.action == "close":
            if alert.status == "closed":
                raise HTTPException(409, "该记录已关闭，可添加备注或重新开启。")
            alert.status, alert.auto_closed = "closed", False
        elif data.action == "reopen":
            if alert.status != "closed":
                raise HTTPException(409, "仅已关闭记录可以重新开启。")
            alert.status, alert.auto_closed = "open", False
        now = utc_now()
        alert.updated_at = now
        history(session, alert, data.action, data.note, now)
        audit(session, f"alerts.action_{data.action}", alert_id=alert_id)
        session.flush()
        return alert_detail(session, alert, person)
