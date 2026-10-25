import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from backend.tests.auth_client import TestClient
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app import alerts
from backend.app.database import AuditEvent, Base
from backend.app.people import Person, PersonStay

TODAY = date(2026, 10, 21)
NOW = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'alerts-test.sqlite3'}", connect_args={"check_same_thread": False, "timeout": 15})
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def client(engine):
    application = FastAPI()
    application.state.engine = engine
    application.include_router(alerts.router)
    with TestClient(application) as client:
        yield client


def seed(engine, days=7, person_id=None, verification="verified", archived=False, entry=None, exit=None):
    with Session(engine) as session, session.begin():
        if person_id is None:
            person_id = str(uuid4())
            session.add(Person(id=person_id, code=f"NIA-TEST-{uuid4().hex}", name="虚构测试人员", nationality="虚构国籍", document_number=uuid4().hex.upper(), source="虚构人员来源", verification=verification, archived=archived))
            session.flush()
        stay_id = str(uuid4())
        session.add(PersonStay(id=stay_id, person_id=person_id, entry_date=entry or TODAY - timedelta(days=90),
                               permitted_until=TODAY + timedelta(days=days) if days is not None else None,
                               exit_date=exit, source="虚构停留来源"))
    return person_id, stay_id


def run(engine, day=TODAY, now=NOW, automatic=False):
    return alerts.scan_alerts(engine, today=day, now=now, automatic=automatic)


def only_alert(engine):
    with Session(engine) as session:
        return session.scalars(select(alerts.Alert)).one()


@pytest.mark.parametrize("days,category,priority", [(-1, "past_due", "urgent"), (0, "due_today", "urgent"), (1, "due_soon", "urgent"), (7, "due_soon", "urgent"), (8, "due_soon", "normal"), (30, "due_soon", "normal"), (None, "needs_review", "review")])
def test_deadline_boundaries_and_unknown_dates(engine, days, category, priority):
    seed(engine, days)
    result = run(engine)
    alert = only_alert(engine)
    assert result["created"] == 1 and result["scanned"] == 1
    assert alert.category == category and alert.priority == priority
    assert alert.days_remaining == days
    if days == 0:
        assert "今日不按逾期" in alert.reason
    if days is None:
        assert alert.deadline is None
        assert "签证有效期" in alert.reason


def test_beyond_window_archived_not_entered_or_departed_are_excluded(engine):
    seed(engine, 31)
    seed(engine, 0, archived=True)
    seed(engine, 1, entry=TODAY + timedelta(days=1))
    seed(engine, -1, exit=TODAY)
    seed(engine, -2, exit=TODAY - timedelta(days=1))
    result = run(engine)
    assert result["scanned"] == 5 and result["created"] == 0
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(alerts.Alert)) == 0


def test_person_pending_multiple_stays_and_future_departure_need_review(engine):
    seed(engine, verification="pending")
    person, _ = seed(engine, 10)
    seed(engine, 20, person_id=person)
    seed(engine, 20, exit=TODAY + timedelta(days=2))
    run(engine)
    with Session(engine) as session:
        items = session.scalars(select(alerts.Alert)).all()
        assert len(items) == 4
        assert all(item.category == "needs_review" for item in items)
        assert all(item.deadline is None and item.days_remaining is None for item in items)
        assert any("尚未完成人工核验" in item.reason for item in items)
        assert any("多条" in item.reason for item in items)
        assert any("未来" in item.reason for item in items)


def test_facts_get_defaults_do_not_create_unverified_record(client, engine):
    _, stay = seed(engine)
    response = client.get(f"/api/alerts/stays/{stay}/facts")
    assert response.status_code == 200
    assert response.json() == {"visa_valid_until": None, "extension_status": "none", "extension_until": None, "receipt_valid_until": None, "verified": False, "source": "", "notes": ""}
    run(engine)
    assert only_alert(engine).category == "due_soon"
    with Session(engine) as session:
        assert session.get(alerts.StayFacts, stay) is None


def test_visa_validity_is_separate_and_never_substitutes_for_stay_deadline(client, engine):
    _, stay = seed(engine, None)
    response = client.put(f"/api/alerts/stays/{stay}/facts", json={"visa_valid_until": (TODAY + timedelta(days=300)).isoformat(), "verified": True, "source": "虚构签证材料"})
    assert response.status_code == 200
    run(engine)
    alert = only_alert(engine)
    assert alert.category == "needs_review" and alert.deadline is None
    assert alert.visa_valid_until == TODAY + timedelta(days=300)
    item = client.get("/api/alerts").json()["items"][0]
    assert item["visa_valid_until"] == (TODAY + timedelta(days=300)).isoformat()


@pytest.mark.parametrize("facts", [
    {"extension_status": "pending", "verified": True, "source": "虚构受理材料"},
    {"receipt_valid_until": "2026-11-15", "verified": True, "source": "虚构回执"},
    {"extension_status": "approved", "extension_until": "2026-11-15", "verified": False, "source": "尚未核实材料"},
    {"source": "新增未核实的补充事实"},
])
def test_pending_receipt_and_unverified_supplements_need_review(client, engine, facts):
    _, stay = seed(engine, -1)
    assert client.put(f"/api/alerts/stays/{stay}/facts", json=facts).status_code == 200
    run(engine)
    item = only_alert(engine)
    assert item.category == "needs_review" and item.days_remaining is None


def test_verified_approved_extension_overrides_baseline_and_clears_old_alert(client, engine):
    _, stay = seed(engine, -1)
    run(engine)
    initial = only_alert(engine)
    assert initial.category == "past_due"
    facts = {"extension_status": "approved", "extension_until": (TODAY + timedelta(days=10)).isoformat(), "verified": True, "source": "虚构已核实批准文件", "receipt_valid_until": (TODAY + timedelta(days=2)).isoformat()}
    assert client.put(f"/api/alerts/stays/{stay}/facts", json=facts).status_code == 200
    run(engine)
    changed = only_alert(engine)
    assert changed.category == "due_soon" and changed.deadline == TODAY + timedelta(days=10)
    facts["extension_until"] = (TODAY + timedelta(days=60)).isoformat()
    assert client.put(f"/api/alerts/stays/{stay}/facts", json=facts).status_code == 200
    result = run(engine)
    closed = only_alert(engine)
    assert result["closed"] == 1 and closed.status == "closed" and closed.auto_closed


def test_editing_original_stay_rechecks_previously_verified_supplements(client, engine):
    _, stay_id = seed(engine, -1)
    assert client.put(f"/api/alerts/stays/{stay_id}/facts", json={"extension_status": "approved", "extension_until": "2026-10-25", "verified": True, "source": "虚构已核实材料"}).status_code == 200
    run(engine)
    assert only_alert(engine).category == "due_soon"
    with Session(engine) as session, session.begin():
        session.get(PersonStay, stay_id).permitted_until = TODAY + timedelta(days=10)
    run(engine)
    item = only_alert(engine)
    assert item.category == "needs_review" and item.deadline is None
    assert "矛盾" in item.reason


@pytest.mark.parametrize("facts,message", [
    ({"verified": True}, "资料来源"),
    ({"verified": True, "extension_status": "approved", "source": "虚构来源"}, "延期截止日期"),
    ({"extension_until": "2020-01-01"}, "不能早于入境"),
    ({"receipt_valid_until": "2020-01-01"}, "不能早于入境"),
    ({"extension_status": "approved", "extension_until": "2026-10-20", "verified": True, "source": "虚构来源"}, "不能早于已登记"),
])
def test_supplement_validation_and_atomicity(client, engine, facts, message):
    _, stay = seed(engine, 7)
    response = client.put(f"/api/alerts/stays/{stay}/facts", json=facts)
    assert response.status_code == 422
    assert message in response.json()["detail"]
    with Session(engine) as session:
        assert session.get(alerts.StayFacts, stay) is None


def test_manual_closed_same_facts_days_and_priority_changes_do_not_reopen(client, engine):
    seed(engine, 8)
    run(engine)
    item = only_alert(engine)
    response = client.post(f"/api/alerts/{item.id}/actions", json={"action": "close", "note": "已完成人工核查"})
    assert response.status_code == 200 and response.json()["status"] == "closed"
    result = run(engine)
    assert result["created"] == 0 and result["updated"] == 0
    run(engine, day=TODAY + timedelta(days=1), now=NOW + timedelta(days=1))
    changed = only_alert(engine)
    assert changed.priority == "urgent" and changed.days_remaining == 7
    assert changed.status == "closed" and not changed.auto_closed
    detail = client.get(f"/api/alerts/{item.id}").json()
    assert [event["action"] for event in detail["history"]] == ["created", "close"]
    run(engine, day=TODAY + timedelta(days=8), now=NOW + timedelta(days=8))
    assert only_alert(engine).status == "open" and only_alert(engine).category == "due_today"


def test_material_deadline_change_reopens_manual_closed_alert(client, engine, monkeypatch):
    _, stay_id = seed(engine, 10)
    run(engine)
    alert = only_alert(engine)
    monkeypatch.setattr(alerts, "utc_now", lambda: NOW + timedelta(minutes=1))
    client.post(f"/api/alerts/{alert.id}/actions", json={"action": "close", "note": "人工处理"})
    with Session(engine) as session, session.begin():
        session.get(PersonStay, stay_id).permitted_until = TODAY + timedelta(days=12)
    run(engine, now=NOW + timedelta(minutes=2))
    assert only_alert(engine).status == "open"
    assert client.get(f"/api/alerts/{alert.id}").json()["history"][-1]["action"] == "auto_reopen"


def test_auto_closure_reappearance_and_history(client, engine):
    person_id, stay_id = seed(engine)
    run(engine)
    alert = only_alert(engine)
    with Session(engine) as session, session.begin():
        session.get(PersonStay, stay_id).exit_date = TODAY
    assert run(engine)["closed"] == 1
    closed = only_alert(engine)
    assert closed.auto_closed and "离境" in closed.reason
    assert run(engine)["closed"] == 0
    with Session(engine) as session, session.begin():
        session.get(PersonStay, stay_id).exit_date = None
    assert run(engine)["updated"] == 1
    assert only_alert(engine).status == "open" and not only_alert(engine).auto_closed
    with Session(engine) as session, session.begin():
        session.get(Person, person_id).archived = True
    run(engine)
    assert "归档" in only_alert(engine).reason
    with Session(engine) as session, session.begin():
        session.get(Person, person_id).archived = False
    run(engine)
    assert only_alert(engine).status == "open"
    actions = [item["action"] for item in client.get(f"/api/alerts/{alert.id}").json()["history"]]
    assert actions.count("created") == 1 and actions.count("auto_close") == 2 and actions.count("auto_reopen") == 2


@pytest.mark.parametrize("disappearance", ["window", "archived", "departed"])
def test_manual_closure_then_disappearance_and_identical_reappearance_reopens(client, engine, disappearance, monkeypatch):
    person_id, stay_id = seed(engine, 10)
    run(engine)
    alert = only_alert(engine)
    original_fingerprint = alert.fingerprint
    monkeypatch.setattr(alerts, "utc_now", lambda: NOW + timedelta(minutes=1))
    client.post(f"/api/alerts/{alert.id}/actions", json={"action": "close", "note": "完成当次核查"})
    with Session(engine) as session, session.begin():
        if disappearance == "window":
            session.get(PersonStay, stay_id).permitted_until = TODAY + timedelta(days=60)
        elif disappearance == "archived":
            session.get(Person, person_id).archived = True
        else:
            session.get(PersonStay, stay_id).exit_date = TODAY
    result = run(engine, now=NOW + timedelta(minutes=2))
    assert result["closed"] == 0 and result["updated"] == 1
    assert only_alert(engine).status == "closed" and only_alert(engine).auto_closed
    unchanged = run(engine, now=NOW + timedelta(minutes=3))
    assert unchanged["closed"] == 0 and unchanged["updated"] == 0
    with Session(engine) as session, session.begin():
        session.get(PersonStay, stay_id).permitted_until = TODAY + timedelta(days=10)
        session.get(PersonStay, stay_id).exit_date = None
        session.get(Person, person_id).archived = False
    assert run(engine, now=NOW + timedelta(minutes=4))["updated"] == 1
    reopened = only_alert(engine)
    assert reopened.fingerprint == original_fingerprint
    assert reopened.status == "open" and not reopened.auto_closed
    actions = [item["action"] for item in client.get(f"/api/alerts/{alert.id}").json()["history"]]
    assert actions == ["created", "close", "auto_close", "auto_reopen"]


def test_action_transitions_notes_history_filters_and_stats(client, engine):
    seed(engine, 0)
    seed(engine, 5)
    run(engine)
    listed = client.get("/api/alerts").json()
    assert listed["total"] == 2 and listed["stats"]["active"] == 2
    alert = listed["items"][0]
    assert client.post(f"/api/alerts/{alert['id']}/actions", json={"action": "start", "note": " "}).status_code == 422
    started = client.post(f"/api/alerts/{alert['id']}/actions", json={"action": "start", "note": "开始资料核对"})
    assert started.json()["status"] == "in_progress"
    noted = client.post(f"/api/alerts/{alert['id']}/actions", json={"action": "note", "note": "补充核查记录"})
    assert noted.json()["status"] == "in_progress" and noted.json()["history"][-1]["note"] == "补充核查记录"
    client.post(f"/api/alerts/{alert['id']}/actions", json={"action": "close", "note": "核查完毕"})
    assert client.get("/api/alerts?status=active").json()["total"] == 1
    assert client.get("/api/alerts?status=closed").json()["total"] == 1
    assert client.post(f"/api/alerts/{alert['id']}/actions", json={"action": "start", "note": "未先重开"}).status_code == 409
    reopened = client.post(f"/api/alerts/{alert['id']}/actions", json={"action": "reopen", "note": "补充核对"})
    assert reopened.json()["status"] == "open"
    assert client.get("/api/alerts?category=due_today").json()["total"] == 1
    assert client.get("/api/alerts?q=%25").json()["total"] == 0
    assert client.get("/api/alerts?page_size=1&page=2").json()["total"] == 2
    assert client.get("/api/alerts/nonexistent").status_code == 404


def test_rules_validation_disabled_only_blocks_automatic_scans(client, engine):
    rule = client.get("/api/alerts/rules").json()
    assert rule["enabled"] and rule["upcoming_days"] == 30 and rule["urgent_days"] == 7
    assert rule["last_scan_at"] is None
    assert client.put("/api/alerts/rules", json={"upcoming_days": 2, "urgent_days": 3}).status_code == 422
    assert client.put("/api/alerts/rules", json={"scan_interval_minutes": 0}).status_code == 422
    changed = client.put("/api/alerts/rules", json={"enabled": False, "upcoming_days": 0, "urgent_days": 0, "scan_interval_minutes": 1})
    assert changed.status_code == 200 and changed.json()["next_scan_at"] is None
    seed(engine, 0)
    assert run(engine, automatic=True) is None
    assert run(engine)["created"] == 1
    assert client.get("/api/alerts/rules").json()["last_scan_at"] is not None


def test_scan_api_uses_business_today_and_rejects_client_override(client, engine, monkeypatch):
    seed(engine, 0)
    monkeypatch.setattr(alerts, "utc_now", lambda: datetime(2026, 10, 20, 18, tzinfo=timezone.utc))
    assert client.post("/api/alerts/scan", json={"today": "2099-01-01"}).status_code == 422
    result = client.post("/api/alerts/scan")
    assert result.status_code == 200 and result.json()["created"] == 1
    assert only_alert(engine).category == "due_today"


def test_concurrent_manual_scans_are_unique_and_automatic_scans_run_once_per_interval(engine):
    seed(engine, 7)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: run(engine), range(4)))
    assert sum(result["created"] for result in results) == 1
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(alerts.Alert)) == 1
        assert session.scalar(select(func.count()).select_from(alerts.AlertHistory)) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: run(engine, automatic=True, now=NOW + timedelta(hours=1)), range(4)))
    assert sum(result is not None for result in results) == 1
    assert run(engine, automatic=True, now=NOW + timedelta(hours=1, minutes=59)) is None


def test_scan_failure_rolls_back_alert_history_audit_and_last_scan(engine):
    seed(engine)
    def fail_audit(session, flush_context, instances):
        if any(isinstance(item, AuditEvent) and item.action == "alerts.scanned" for item in session.new):
            raise SQLAlchemyError("sensitive diagnostic")
    event.listen(Session, "before_flush", fail_audit)
    try:
        with pytest.raises(SQLAlchemyError):
            run(engine)
    finally:
        event.remove(Session, "before_flush", fail_audit)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(alerts.Alert)) == 0
        assert session.scalar(select(func.count()).select_from(alerts.AlertHistory)) == 0
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert session.get(alerts.AlertRule, 1) is None


def test_audits_contain_only_identifiers_and_counts(client, engine):
    _, stay = seed(engine)
    client.put(f"/api/alerts/stays/{stay}/facts", json={"verified": True, "source": "私有测试来源不进审计", "notes": "私有测试备注"})
    run(engine)
    item = only_alert(engine)
    client.post(f"/api/alerts/{item.id}/actions", json={"action": "note", "note": "私有处理说明"})
    with Session(engine) as session:
        items = session.scalars(select(AuditEvent)).all()
        for item in items:
            assert all(key.endswith("_id") or key == "count" or key.endswith("_count") for key in item.details)
        content = json.dumps([item.details for item in items], ensure_ascii=False)
        assert "私有" not in content


def test_scheduler_reports_failure_recovers_and_stops_without_waiting_30_seconds(engine, monkeypatch):
    async def scenario():
        application = SimpleNamespace(state=SimpleNamespace(engine=engine))
        stop = asyncio.Event()
        real_scan = alerts.scan_alerts
        calls = []
        def failing_scan(*args, **kwargs):
            calls.append(1)
            raise SQLAlchemyError("private connection string")
        monkeypatch.setattr(alerts, "scan_alerts", failing_scan)
        task = asyncio.create_task(alerts.scheduler_loop(application, stop))
        for _ in range(100):
            if getattr(application.state, "alerts_last_error", None):
                break
            await asyncio.sleep(.01)
        assert calls and application.state.alerts_last_error == alerts.SCAN_FAILURE
        stop.set()
        await asyncio.wait_for(task, timeout=1)
        with Session(engine) as session:
            assert session.get(alerts.AlertRule, 1).last_error == alerts.SCAN_FAILURE
        monkeypatch.setattr(alerts, "scan_alerts", real_scan)
        stop = asyncio.Event()
        task = asyncio.create_task(alerts.scheduler_loop(application, stop))
        for _ in range(100):
            if application.state.alerts_last_error is None:
                break
            await asyncio.sleep(.01)
        stop.set()
        await asyncio.wait_for(task, timeout=1)
        with Session(engine) as session:
            rule = session.get(alerts.AlertRule, 1)
            assert rule.last_error is None and rule.last_scan_at is not None
    asyncio.run(scenario())
