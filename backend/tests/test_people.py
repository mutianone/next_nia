import csv
from io import StringIO
import json

from backend.tests.auth_client import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app import main
from backend.app.database import AuditEvent
from backend.app.people import CSV_COLUMNS, router


def application(tmp_path):
    app = main.create_app(tmp_path)
    if not any(getattr(route, "path", None) == "/api/people" for route in app.routes):
        app.include_router(router)
    return app


@pytest.fixture
def client(tmp_path):
    with TestClient(application(tmp_path)) as instance:
        yield instance


def person_payload(**updates):
    return {"name": "虚构测试人员甲", "nationality": "示例国籍", "document_number": "TEST-P-001",
            "source": "自动化测试虚构资料", **updates}


def create(client, **updates):
    response = client.post("/api/people", json=person_payload(**updates))
    assert response.status_code == 201, response.text
    return response.json()


def csv_text(rows, fields=None):
    fields = fields or ["name", "nationality", "document_number", "source"]
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def test_create_edit_stay_archive_and_restart_preserve_factual_data(tmp_path):
    with TestClient(application(tmp_path)) as client:
        person = create(client, document_number=" test p 001 ", birth_date="1990-01-01")
        assert person["code"].startswith("NIA-")
        assert person["document_number"] == "TESTP001"
        assert person["stays"] == []
        assert person["created_at"].endswith("+00:00")
        identifier = person["id"]
        edited = client.put(f"/api/people/{identifier}", json=person_payload(document_number="TESTP001", birth_date="1990-01-01", verification="verified", notes="人工核验事实"))
        assert edited.status_code == 200
        assert edited.json()["code"] == person["code"]
        assert edited.json()["verification"] == "verified"
        stay = client.post(f"/api/people/{identifier}/stays", json={"entry_date": "2026-01-01", "source": "虚构入境记录"})
        assert stay.status_code == 201
        assert stay.json()["permitted_until"] is None
        assert "illegal" not in stay.text
        edited_stay = client.put(f"/api/people/{identifier}/stays/{stay.json()['id']}", json={"entry_date": "2026-01-01", "permitted_until": "2026-01-10", "exit_date": "2026-01-20", "source": "人工补录虚构离境事实"})
        assert edited_stay.status_code == 200
        assert edited_stay.json()["exit_date"] == "2026-01-20"
        archived = client.post(f"/api/people/{identifier}/archive", json={"archived": True})
        assert archived.status_code == 200
        assert archived.json()["archived"] is True
        assert len(archived.json()["stays"]) == 1
        assert client.get("/api/people").json()["total"] == 0
        assert client.get("/api/people?archived=true").json()["total"] == 1
        assert client.post(f"/api/people/{identifier}/archive", json={"archived": False}).json()["archived"] is False
    with TestClient(application(tmp_path)) as client:
        restored = client.get(f"/api/people/{identifier}").json()
        assert restored["notes"] == "人工核验事实"
        assert restored["stays"][0]["exit_date"] == "2026-01-20"
        assert restored["archived"] is False
        stats = client.get("/api/people").json()["stats"]
        assert stats == {"total": 1, "verified": 1, "pending": 0, "archived": 0}


def test_document_uniqueness_normalizes_case_whitespace_and_includes_archived(client):
    person = create(client, document_number="a b 123")
    assert client.post("/api/people", json=person_payload(document_number=" AB123 ")).status_code == 409
    client.post(f"/api/people/{person['id']}/archive", json={"archived": True})
    assert client.post("/api/people", json=person_payload(document_number="ab123")).status_code == 409
    other = create(client, document_number="AB123", document_type="other")
    assert other["id"] != person["id"]
    response = client.put(f"/api/people/{other['id']}", json=person_payload(document_number="AB123", document_type="passport"))
    assert response.status_code == 409
    assert client.get(f"/api/people/{other['id']}").json()["document_type"] == "other"


@pytest.mark.parametrize("updates,message", [
    ({"name": "   "}, "姓名"),
    ({"source": ""}, "资料来源"),
    ({"document_number": " \t "}, "证件号码"),
    ({"birth_date": "not-a-date"}, "出生日期"),
    ({"birth_date": "2999-01-01"}, "出生日期不能晚于今天"),
    ({"birth_date": "1990-01-01", "document_expiry": "1980-01-01"}, "证件有效期不能早于出生日期"),
    ({"gender": "invalid"}, "性别"),
    ({"code": "assigned-by-client"}, "只读"),
])
def test_person_validation_is_clear_and_does_not_echo_personal_values(client, updates, message):
    response = client.post("/api/people", json=person_payload(**updates))
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert "虚构测试人员甲" not in response.text
    assert client.get("/api/people").json()["total"] == 0


@pytest.mark.parametrize("stay,message", [
    ({"entry_date": "2026-01-10", "exit_date": "2026-01-09"}, "离境日期不能早于入境日期"),
    ({"entry_date": "2026-01-10", "permitted_until": "2026-01-09"}, "允许停留截止日期不能早于入境日期"),
    ({"entry_date": "1980-01-01"}, "入境日期不能早于该人员出生日期"),
])
def test_stay_date_relations_are_validated_without_partial_write(client, stay, message):
    person = create(client, birth_date="1990-01-01")
    response = client.post(f"/api/people/{person['id']}/stays", json={**stay, "source": "虚构测试来源"})
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert client.get(f"/api/people/{person['id']}").json()["stays"] == []


def test_person_birth_date_cannot_conflict_with_existing_stay(client):
    person = create(client)
    client.post(f"/api/people/{person['id']}/stays", json={"entry_date": "2000-01-01", "source": "虚构测试来源"})
    response = client.put(f"/api/people/{person['id']}", json=person_payload(birth_date="2001-01-01"))
    assert response.status_code == 422
    assert "已有停留记录" in response.json()["detail"]
    assert client.get(f"/api/people/{person['id']}").json()["birth_date"] is None


def test_stay_cannot_be_edited_through_another_person(client):
    first = create(client)
    second = create(client, document_number="TEST-P-002")
    stay = client.post(f"/api/people/{first['id']}/stays", json={"entry_date": "2026-01-01", "source": "虚构测试来源"}).json()
    response = client.put(f"/api/people/{second['id']}/stays/{stay['id']}", json={"entry_date": "2026-02-01", "source": "虚构测试来源"})
    assert response.status_code == 404
    assert client.get(f"/api/people/{first['id']}").json()["stays"][0]["entry_date"] == "2026-01-01"


def test_search_pagination_archive_filters_and_statistics(client):
    for index in range(4):
        person = create(client, name=f"示例{index}", english_name="Example Person" if index == 1 else "", document_number=f"SEARCH-{index}", verification="verified" if index == 2 else "pending")
        if index == 3:
            client.post(f"/api/people/{person['id']}/archive", json={"archived": True})
    page1 = client.get("/api/people?page_size=2").json()
    page2 = client.get("/api/people?page_size=2&page=2").json()
    assert page1["total"] == 3
    assert len(page1["items"]) == 2 and len(page2["items"]) == 1
    assert not set(person["id"] for person in page1["items"]) & set(person["id"] for person in page2["items"])
    assert page1["stats"] == {"total": 4, "verified": 1, "pending": 2, "archived": 1}
    assert client.get("/api/people", params={"q": "example"}).json()["total"] == 1
    assert client.get("/api/people", params={"q": "search - 2"}).json()["total"] == 1
    assert client.get("/api/people", params={"q": "%"}).json()["total"] == 0
    assert client.get("/api/people?archived=true").json()["total"] == 1
    assert client.get("/api/people?page=0").status_code == 422
    assert client.get("/api/people?page_size=101").status_code == 422


def test_csv_preview_normalizes_and_does_not_write(client):
    text = "\ufeff" + csv_text([person_payload(document_number=" csv x 001 ")])
    preview = client.post("/api/people/import/preview", json={"csv_text": text})
    assert preview.status_code == 200
    result = preview.json()
    assert result["total"] == result["valid_count"] == 1
    assert result["errors"] == []
    assert result["rows"][0]["document_number"] == "CSVX001"
    assert result["rows"][0]["gender"] == "unknown"
    assert result["rows"][0]["birth_date"] is None
    assert client.get("/api/people").json()["total"] == 0
    imported = client.post("/api/people/import", json={"csv_text": text})
    assert imported.status_code == 200
    assert imported.json() == {"imported": 1}
    assert client.get("/api/people").json()["total"] == 1


def test_csv_partial_validation_error_leaves_all_rows_unwritten(client):
    text = csv_text([person_payload(document_number="CSV-VALID"), person_payload(document_number="CSV-INVALID", source="")])
    preview = client.post("/api/people/import/preview", json={"csv_text": text}).json()
    assert preview["total"] == 2 and preview["valid_count"] == 1
    assert preview["errors"][0]["row"] == 3
    response = client.post("/api/people/import", json={"csv_text": text})
    assert response.status_code == 422
    assert "未写入任何资料" in response.json()["detail"]
    assert client.get("/api/people").json()["total"] == 0


@pytest.mark.parametrize("existing", [False, True])
def test_csv_duplicate_is_conflict_and_import_is_atomic(client, existing):
    if existing:
        create(client, document_number="CSV-DUP")
    text = csv_text([person_payload(document_number="CSV-VALID"), person_payload(document_number="CSV-DUP"), person_payload(document_number="csv-dup")])
    preview = client.post("/api/people/import/preview", json={"csv_text": text}).json()
    assert any("重复" in item["message"] or "已存在" in item["message"] for item in preview["errors"])
    response = client.post("/api/people/import", json={"csv_text": text})
    assert response.status_code == 409
    assert client.get("/api/people").json()["total"] == int(existing)


def test_csv_structural_errors_and_size_limits(client):
    for invalid in ["", "name,nationality,document_number\n示例,示例,CSV-1", "name,name,nationality,document_number,source\na,b,c,d,e", 'name,nationality,document_number,source\n"broken,示例,CSV-1,示例']:
        preview = client.post("/api/people/import/preview", json={"csv_text": invalid})
        assert preview.status_code == 200
        assert preview.json()["errors"]
        assert client.post("/api/people/import", json={"csv_text": invalid}).status_code == 422
    large = "汉" * 400_000
    assert client.post("/api/people/import/preview", json={"csv_text": large}).status_code == 413
    many = csv_text([person_payload(document_number=f"ROW-{index}") for index in range(501)])
    assert client.post("/api/people/import", json={"csv_text": many}).status_code == 413
    assert client.get("/api/people").json()["total"] == 0


def test_csv_template_is_read_only_and_export_respects_filter_and_formula_safety(client):
    template = client.get("/api/people/template.csv")
    assert template.status_code == 200
    assert template.content.startswith(b"\xef\xbb\xbf")
    assert "示例" in template.text
    assert "__nia_csv_format" not in template.text
    assert client.get("/api/people").json()["total"] == 0
    create(client, name="=FORMULA()", phone="+123456", document_number="EXPORT-1", notes="  @formula()", address="line 1,\nline 2")
    person = create(client, document_number="EXPORT-2")
    client.post(f"/api/people/{person['id']}/archive", json={"archived": True})
    response = client.get("/api/people/export.csv", params={"q": "EXPORT-1"})
    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))
    assert len(rows) == 1
    assert rows[0]["name"] == "'=FORMULA()"
    assert rows[0]["phone"] == "'+123456"
    assert rows[0]["notes"].startswith("'")
    assert rows[0]["address"] == "line 1,\nline 2"
    assert rows[0]["__nia_csv_format"] == "safe-v1"
    archived = list(csv.DictReader(StringIO(client.get("/api/people/export.csv?archived=true").content.decode("utf-8-sig"))))
    assert [row["document_number"] for row in archived] == ["EXPORT-2"]


def test_csv_export_import_roundtrip_preserves_formulas_and_literal_apostrophes(tmp_path):
    with TestClient(application(tmp_path / "source")) as source:
        original = create(source, name="=原始姓名", phone="+8613800000000", notes="@原始备注",
                          english_name="'首单引号", address="'+原文", source="'", birth_date="1990-01-01")
        exported = source.get("/api/people/export.csv").content.decode("utf-8-sig")
        row = next(csv.DictReader(StringIO(exported)))
        assert row["english_name"] == "''首单引号"
        assert row["address"] == "''+原文"
        assert row["source"] == "''"
    with TestClient(application(tmp_path / "destination")) as destination:
        preview = destination.post("/api/people/import/preview", json={"csv_text": exported}).json()
        assert preview["errors"] == []
        assert preview["rows"][0] == {column: original[column] for column in CSV_COLUMNS}
        response = destination.post("/api/people/import", json={"csv_text": exported})
        assert response.status_code == 200, response.text
        restored = destination.get("/api/people").json()["items"][0]
        assert {column: restored[column] for column in CSV_COLUMNS} == {column: original[column] for column in CSV_COLUMNS}


@pytest.mark.parametrize("original", ["'原文", "'+原文", "'=原文", "''原文", "'"])
def test_unmarked_csv_preserves_original_apostrophes(client, original):
    text = csv_text([person_payload(name=original)])
    preview = client.post("/api/people/import/preview", json={"csv_text": text}).json()
    assert preview["errors"] == []
    assert preview["rows"][0]["name"] == original
    assert client.post("/api/people/import", json={"csv_text": text}).status_code == 200
    assert client.get("/api/people").json()["items"][0]["name"] == original


@pytest.mark.parametrize("marker", ["", "safe-v2", " safe-v1", "safe-v1 "])
def test_csv_invalid_safety_marker_rejects_whole_import(client, marker):
    fields = ["name", "nationality", "document_number", "source", "__nia_csv_format"]
    text = csv_text([person_payload(document_number="VALID", __nia_csv_format="safe-v1"),
                     person_payload(document_number="INVALID", __nia_csv_format=marker)], fields)
    preview = client.post("/api/people/import/preview", json={"csv_text": text}).json()
    assert preview["errors"][0]["row"] == 3
    assert "安全格式标记无效" in preview["errors"][0]["message"]
    assert client.post("/api/people/import", json={"csv_text": text}).status_code == 422
    assert client.get("/api/people").json()["total"] == 0


@pytest.mark.parametrize("edited", ["'普通姓名", "+FORMULA()", "=FORMULA()", "@FORMULA()", "-FORMULA()", "\t=FORMULA()"])
def test_marked_csv_rejects_noncanonical_edited_cells(client, edited):
    fields = ["name", "nationality", "document_number", "source", "__nia_csv_format"]
    text = csv_text([person_payload(name=edited, __nia_csv_format="safe-v1")], fields)
    preview = client.post("/api/people/import/preview", json={"csv_text": text}).json()
    assert preview["errors"][0]["row"] == 2
    assert "安全编码不完整" in preview["errors"][0]["message"]
    assert client.post("/api/people/import", json={"csv_text": text}).status_code == 422
    assert client.get("/api/people").json()["total"] == 0


def test_person_and_audit_write_are_one_transaction(client):
    def reject_audit(session, flush_context, instances):
        if any(isinstance(item, AuditEvent) and item.action == "person.created" for item in session.new):
            raise SQLAlchemyError("private database diagnostic must not appear")

    event.listen(Session, "before_flush", reject_audit)
    try:
        response = client.post("/api/people", json=person_payload())
    finally:
        event.remove(Session, "before_flush", reject_audit)
    assert response.status_code == 503
    assert "private database diagnostic" not in response.text
    assert client.get("/api/people").json()["total"] == 0


def test_audits_contain_only_ids_and_counts(client):
    person = create(client, name="虚构隐私测试名", document_number="AUDIT-PRIVATE-001")
    client.post(f"/api/people/{person['id']}/archive", json={"archived": True})
    client.post(f"/api/people/{person['id']}/stays", json={"entry_date": "2026-01-01", "source": "测试来源详情不应进入审计"})
    text = csv_text([person_payload(document_number="AUDIT-IMPORT-001")])
    assert client.post("/api/people/import", json={"csv_text": text}).status_code == 200
    with Session(client.app.state.engine) as session:
        events = session.scalars(select(AuditEvent).where(AuditEvent.action.in_({"person.created", "person.archived", "person.stay_created", "people.imported"}))).all()
        assert {item.action for item in events} >= {"person.created", "person.archived", "person.stay_created", "people.imported"}
        for item in events:
            assert set(item.details).issubset({"person_id", "stay_id", "count"})
        serialized = json.dumps([item.details for item in events], ensure_ascii=False)
        assert "虚构隐私测试名" not in serialized
        assert "AUDIT-PRIVATE" not in serialized
        assert "测试来源详情" not in serialized


def test_not_found_and_cross_site_write(client):
    assert client.get("/api/people/missing").status_code == 404
    assert client.post("/api/people/missing/stays", json={"entry_date": "2026-01-01", "source": "虚构来源"}).status_code == 404
    response = client.post("/api/people", json=person_payload(), headers={"origin": "https://unrelated.invalid"})
    assert response.status_code == 403
    assert client.get("/api/people").json()["total"] == 0
