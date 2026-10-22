from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from threading import Barrier
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app import main
from backend.app.database import AuditEvent
from backend.app.documents import DRAFT_LABEL, REVIEWED_LABEL, DocumentDraft, DocumentTemplate, INTERNAL_ID, router, seed_templates


def application(path):
    app = main.create_app(path)
    if not any(getattr(route, "path", None) == "/api/documents" for route in app.routes):
        app.include_router(router)
    return app


@pytest.fixture
def client(tmp_path):
    with TestClient(application(tmp_path)) as instance:
        seed_templates(instance.app.state.engine)
        yield instance


def person(client, suffix="001", **updates):
    response = client.post("/api/people", json={"name": "虚构核查人员", "nationality": "示例国籍", "document_number": "TEST-" + suffix,
        "source": "自动化测试虚构资料", **updates})
    assert response.status_code == 201, response.text
    return response.json()


def template(client, **updates):
    response = client.post("/api/documents/templates", json={"name": "测试模板", "version": "1", "kind": "official", "source": "业务方提供的测试来源",
        "body": "测试格式（无真实法定效力）\n姓名：{{person_name}}\n事实：{{facts}}", **updates})
    assert response.status_code == 201, response.text
    return response.json()


def approve(client, value):
    response = client.patch(f"/api/documents/templates/{value['id']}", json={"expected_revision": value["revision"], "status": "approved", "review_note": "仅为自动化测试人工确认", "confirmed": True})
    assert response.status_code == 200, response.text
    return response.json()


def draft(client, template_value=None, person_value=None, **updates):
    template_value = template_value or template(client)
    person_value = person_value or person(client)
    response = client.post("/api/documents", json={"template_id": template_value["id"], "person_id": person_value["id"], "title": "测试核查材料",
        "fields": {"facts": "虚构事实"}, **updates})
    assert response.status_code == 201, response.text
    return response.json()


def review(client, value, **updates):
    return client.patch(f"/api/documents/{value['id']}", json={"expected_revision": value["revision"], "status": "reviewed", "review_note": "人工核对测试快照及正文", "confirmed": True, **updates})


def test_internal_seed_is_unique_approved_and_not_official(client):
    seed_templates(client.app.state.engine)
    rows = client.get("/api/documents/templates").json()["items"]
    assert len(rows) == 1
    item = rows[0]
    assert item["id"] == INTERNAL_ID and item["kind"] == "internal" and item["status"] == "approved"
    assert "不是法定文书" in item["source"] and "非执法决定" in item["body"]
    variables = client.get("/api/documents/templates/variables").json()["items"]
    assert len(variables) == 16
    assert next(item for item in variables if item["name"] == "organization")["source"] == "manual"
    archived = client.patch(f"/api/documents/templates/{INTERNAL_ID}", json={"expected_revision": 1, "status": "archived"})
    assert archived.status_code == 200
    seed_templates(client.app.state.engine)
    assert client.get(f"/api/documents/templates/{INTERNAL_ID}").json()["status"] == "archived"


def test_template_approval_requires_source_note_confirmation_and_freezes_content(client):
    item = template(client, source="")
    path = f"/api/documents/templates/{item['id']}"
    for change in [{"status": "approved"}, {"status": "approved", "source": "已核实来源", "confirmed": True},
                   {"status": "approved", "source": "已核实来源", "review_note": "核实说明"}]:
        response = client.patch(path, json={"expected_revision": 1, **change})
        assert response.status_code == 422
        assert client.get(path).json()["status"] == "draft"
    changed = client.patch(path, json={"expected_revision": 1, "source": "已核实来源"}).json()
    approved = approve(client, changed)
    for key, value in {"body": "修改正文", "name": "新名字", "version": "2", "kind": "internal", "source": "新来源", "review_note": "改写复核意见", "status": "draft"}.items():
        assert client.patch(path, json={"expected_revision": approved["revision"], key: value}).status_code == 409
    duplicate = client.post("/api/documents/templates", json={"name": item["name"], "version": "1", "kind": "official", "body": "正文"})
    assert duplicate.status_code == 409
    assert template(client, version="2")["version"] == "2"


@pytest.mark.parametrize("body", ["{{unknown}}", "{{person_name.__class__}}", "{{ 7*7 }}", "{% if x %}正文{% endif %}", "{# hidden #}", "{{person_name}", "正文}}", "{{facts}}" * 201, "正文\x01"])
def test_template_rejects_unknown_variables_expressions_and_controls(client, body):
    response = client.post("/api/documents/templates", json={"name": "不安全模板", "version": "1", "kind": "internal", "body": body})
    assert response.status_code == 422
    assert len(client.get("/api/documents/templates").json()["items"]) == 1


def test_snapshots_are_stable_and_unapproved_or_changed_template_cannot_be_reviewed(client):
    p = person(client)
    t = template(client)
    d = draft(client, t, p)
    assert review(client, d).status_code == 409
    edit = client.patch(f"/api/documents/templates/{t['id']}", json={"expected_revision": 1, "body": "新的正文 {{person_name}} / {{facts}}"}).json()
    approve(client, edit)
    person_edit = {key: p[key] for key in ("name", "nationality", "document_number", "source")}
    person_edit["name"] = "虚构变更后的姓名"
    assert client.put(f"/api/people/{p['id']}", json=person_edit).status_code == 200
    current = client.get(f"/api/documents/{d['id']}").json()
    assert current["template_snapshot"] == d["template_snapshot"]
    assert current["person_snapshot"] == d["person_snapshot"]
    assert "虚构核查人员" in current["rendered_text"] and "新的正文" not in current["rendered_text"]
    assert client.get("/api/documents", params={"q": "虚构核查人员"}).json()["total"] == 1
    assert client.get("/api/documents", params={"q": p["code"]}).json()["total"] == 1
    assert client.get("/api/documents", params={"q": "虚构变更后的姓名"}).json()["total"] == 0
    assert review(client, d).status_code == 409


def test_missing_stay_variables_allow_draft_but_prevent_review_and_export(client):
    t = client.get(f"/api/documents/templates/{INTERNAL_ID}").json()
    p = person(client)
    stay = client.post(f"/api/people/{p['id']}/stays", json={"entry_date": "2026-01-01", "source": "虚构停留记录"}).json()
    d = draft(client, t, p, stay_id=stay["id"], fields={"facts": "事实待核对", "organization": "测试单位", "officer": "测试承办人"})
    assert d["unresolved_fields"] == ["permitted_until"]
    assert "{{permitted_until}}" in d["rendered_text"]
    assert review(client, d).status_code == 422
    for suffix in ("export.txt", "export.docx"):
        assert client.get(f"/api/documents/{d['id']}/{suffix}").status_code == 422
    assert client.post(f"/api/people/{p['id']}/stays", json={"entry_date": "2026-02-01", "permitted_until": "2026-04-01", "source": "新增记录"}).status_code == 201
    assert client.get(f"/api/documents/{d['id']}").json()["unresolved_fields"] == ["permitted_until"]


def test_review_is_immutable_copy_retains_snapshots_and_archive_is_read_only(client):
    t = approve(client, template(client))
    d = draft(client, t, fields={"facts": "核对正文", "review_note": "正文变量说明"})
    for override in [{"confirmed": False}, {"review_note": ""}]:
        assert review(client, d, **override).status_code == 422
    reviewed = review(client, d).json()
    assert reviewed["status"] == "reviewed" and reviewed["reviewed_at"]
    assert reviewed["review_note"] != reviewed["fields"]["review_note"]
    assert [item["action"] for item in reviewed["history"]] == ["created", "reviewed"]
    assert reviewed["history"][-1]["note"] == reviewed["review_note"]
    path = f"/api/documents/{d['id']}"
    for update in [{"title": "改变标题"}, {"fields": {"facts": "改变正文"}}, {"status": "draft"}, {"review_note": "改意见"}]:
        assert client.patch(path, json={"expected_revision": reviewed["revision"], **update}).status_code == 409
    copied = client.post(path + "/copy").json()
    assert copied["id"] != d["id"] and copied["status"] == "draft" and copied["revision"] == 1
    assert copied["template_snapshot"] == reviewed["template_snapshot"] and copied["person_snapshot"] == reviewed["person_snapshot"]
    assert copied["fields"] == reviewed["fields"] and copied["review_note"] == "" and copied["reviewed_at"] is None
    archived = client.patch(path, json={"expected_revision": reviewed["revision"], "status": "archived"}).json()
    assert archived["status"] == "archived"
    assert client.patch(path, json={"expected_revision": archived["revision"], "status": "draft"}).status_code == 409
    assert client.get(path + "/export.txt").status_code == 409
    assert client.get(path + "/export.docx").status_code == 409
    assert client.post(path + "/copy").status_code == 201
    listing = client.get("/api/documents", params={"status": "archived", "q": "测试核查"}).json()
    assert listing["total"] == 1 and listing["stats"] == {"total": 3, "drafts": 2, "reviewed": 0, "archived": 1}


def test_revoking_source_template_blocks_existing_draft_review(client):
    t = approve(client, template(client))
    d = draft(client, t)
    response = client.patch(f"/api/documents/templates/{t['id']}", json={"expected_revision": t["revision"], "status": "archived"})
    assert response.status_code == 200
    assert review(client, d).status_code == 409
    assert client.post("/api/documents", json={"template_id": t["id"], "person_id": d["person_id"], "title": "不可新建"}).status_code == 409


def test_txt_docx_exports_have_persistent_labels_and_safe_plain_text(client):
    t = approve(client, template(client))
    d = draft(client, t, title='测试 <标题> & "文本"', fields={"facts": '<script>alert("test")</script> & {{not_executed}}\n' + "虚构长段落\n" * 200})
    path = f"/api/documents/{d['id']}"
    assert DRAFT_LABEL not in d["rendered_text"]
    for label in (DRAFT_LABEL, REVIEWED_LABEL):
        txt = client.get(path + "/export.txt")
        assert txt.status_code == 200 and label in txt.content.decode("utf-8-sig")
        assert txt.headers["content-disposition"] == 'attachment; filename="document.txt"'
        response = client.get(path + "/export.docx")
        assert response.status_code == 200 and response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-disposition"] == 'attachment; filename="document.docx"'
        with ZipFile(BytesIO(response.content)) as archive:
            assert set(archive.namelist()) == {"[Content_Types].xml", "_rels/.rels", "word/_rels/document.xml.rels", "word/header1.xml", "word/document.xml", "word/styles.xml"}
            for filename in archive.namelist():
                ET.fromstring(archive.read(filename))
                assert b'TargetMode="External"' not in archive.read(filename)
            body = archive.read("word/document.xml").decode()
            header = archive.read("word/header1.xml").decode()
            assert label in body and label in header
            assert "headerReference" in body and "&lt;script&gt;" in body and "{{not_executed}}" in body
            assert "<script>" not in body
            assert '<w:pStyle w:val="Title"/>' in body and '<w:color w:val="000000"/>' in body
        if label == DRAFT_LABEL:
            assert review(client, d).status_code == 200


@pytest.mark.parametrize("fields", [{"person_name": "覆盖身份"}, {"entry_date": "2020-01-01"}, {"unknown": "不支持"}, {"document_date": "20260101"}, {"deadline": "2026-02-30"}, {"facts": "超" * 6001}, {"facts": "非法\x08"}, {"facts": 123}])
def test_manual_fields_are_allowlisted_typed_and_bounded(client, fields):
    t = template(client)
    p = person(client)
    response = client.post("/api/documents", json={"template_id": t["id"], "person_id": p["id"], "title": "测试", "fields": fields})
    assert response.status_code == 422
    assert client.get("/api/documents").json()["total"] == 0


def test_date_order_prevents_review_and_export_but_keeps_draft(client):
    t = approve(client, template(client))
    d = draft(client, t, fields={"facts": "说明", "document_date": "2026-10-01", "deadline": "2026-09-30"})
    assert review(client, d).status_code == 422
    assert client.get(f"/api/documents/{d['id']}/export.txt").status_code == 422


def test_wrong_person_stay_and_unknown_extra_fields_are_rejected(client):
    t = template(client)
    first = person(client)
    other = person(client, "002")
    stay = client.post(f"/api/people/{other['id']}/stays", json={"entry_date": "2026-01-01", "source": "测试来源"}).json()
    payload = {"template_id": t["id"], "person_id": first["id"], "title": "错误关系"}
    assert client.post("/api/documents", json={**payload, "stay_id": stay["id"]}).status_code == 422
    assert client.post("/api/documents", json={**payload, "template_snapshot": {}}).status_code == 422
    assert client.post("/api/documents", json={**payload, "fields": {"facts": "说明"}, "status": "reviewed"}).status_code == 422


def test_alert_link_must_match_both_person_and_stay(client):
    from backend.app.alerts import Alert

    t = template(client)
    first, second = person(client), person(client, "002")
    stay_ids = [client.post(f"/api/people/{p['id']}/stays", json={"entry_date": "2026-01-01", "source": "测试来源"}).json()["id"] for p in (first, second)]
    with Session(client.app.state.engine) as session, session.begin():
        alert = Alert(person_id=first["id"], stay_id=stay_ids[0], category="needs_review", reason="虚构待核查事项", fingerprint="test-link")
        session.add(alert)
        session.flush()
        alert_id = alert.id
    payload = {"template_id": t["id"], "person_id": first["id"], "stay_id": stay_ids[0], "alert_id": alert_id, "title": "关联测试"}
    assert client.post("/api/documents", json={**payload, "person_id": second["id"], "stay_id": stay_ids[1]}).status_code == 422
    assert client.post("/api/documents", json={**payload, "stay_id": None}).status_code == 422
    assert client.post("/api/documents", json={**payload, "alert_id": "unknown"}).status_code == 422
    response = client.post("/api/documents", json=payload)
    assert response.status_code == 201
    assert response.json()["alert_id"] == alert_id


def test_template_simultaneous_updates_preserve_revision(client):
    t = template(client)
    path = f"/api/documents/templates/{t['id']}"
    barrier = Barrier(2)

    def update(body):
        barrier.wait(timeout=5)
        return client.patch(path, json={"expected_revision": t["revision"], "body": body})

    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(update, ["并发正文甲", "并发正文乙"]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json() for response in responses if response.status_code == 200)
    assert client.get(path).json()["body"] == winner["body"]
    assert client.get(path).json()["revision"] == 2


def test_missing_revision_and_simultaneous_updates_do_not_lose_writes(client):
    d = draft(client)
    path = f"/api/documents/{d['id']}"
    assert client.patch(path, json={"title": "无版本"}).status_code == 422
    barrier = Barrier(2)

    def update(title):
        barrier.wait(timeout=5)
        return client.patch(path, json={"expected_revision": d["revision"], "title": title})

    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(update, ["并发甲", "并发乙"]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json() for response in responses if response.status_code == 200)
    current = client.get(path).json()
    assert current["title"] == winner["title"] and current["revision"] == 2
    assert len(current["history"]) == 2


def test_document_mutation_and_audit_are_atomic_and_private(client):
    t = template(client)
    p = person(client)

    def reject(session, flush_context, instances):
        if any(isinstance(item, AuditEvent) and item.action == "document.created" for item in session.new):
            raise SQLAlchemyError("private database diagnostic")

    event.listen(Session, "before_flush", reject)
    try:
        response = client.post("/api/documents", json={"template_id": t["id"], "person_id": p["id"], "title": "不应写入", "fields": {"facts": "隐私事实"}})
    finally:
        event.remove(Session, "before_flush", reject)
    assert response.status_code == 503 and "private database diagnostic" not in response.text
    assert client.get("/api/documents").json()["total"] == 0
    d = draft(client, t, p, fields={"facts": "隐私事实"})
    assert client.get(f"/api/documents/{d['id']}/export.txt").status_code == 200
    with Session(client.app.state.engine) as session:
        events = session.scalars(select(AuditEvent).where(AuditEvent.action.startswith("document."))).all()
        assert all(set(item.details) <= {"template_id", "document_id", "revision"} for item in events)
        serialized = json.dumps([item.details for item in events], ensure_ascii=False)
        assert "隐私事实" not in serialized and "虚构核查人员" not in serialized


def test_documents_are_isolated_between_active_databases_and_persist_on_restart(tmp_path):
    with TestClient(application(tmp_path / "first")) as client:
        d = draft(client)
    with TestClient(application(tmp_path / "second")) as client:
        assert client.get("/api/documents").json()["total"] == 0
        assert client.get(f"/api/documents/{d['id']}").status_code == 404
    with TestClient(application(tmp_path / "first")) as client:
        restored = client.get(f"/api/documents/{d['id']}").json()
        assert restored["template_snapshot"] == d["template_snapshot"]
        assert restored["person_snapshot"] == d["person_snapshot"]
        assert restored["rendered_text"] == d["rendered_text"]


def test_register_missing_fields_match_saved_snapshot_and_update_after_save(client):
    t = template(client, body="姓名：{{person_name}} 入境：{{entry_date}} 事实：{{facts}} 经办：{{officer}}")
    d = draft(client, t, fields={"facts": "已经填写"})
    row = client.get('/api/documents').json()['items'][0]
    assert row['unresolved_fields'] == d['unresolved_fields'] == ['entry_date', 'officer']
    assert 'rendered_text' not in row
    updated = client.patch(f"/api/documents/{d['id']}", json={"expected_revision": d['revision'], "fields": {"facts": "已经填写", "officer": "经办人员"}})
    assert updated.status_code == 200
    assert client.get('/api/documents').json()['items'][0]['unresolved_fields'] == ['entry_date']
