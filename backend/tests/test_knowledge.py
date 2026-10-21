from io import BytesIO
import json
from unittest.mock import Mock
import zipfile

from fastapi.testclient import TestClient
import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app import knowledge, knowledge_ai, retrieval
from backend.app.database import AuditEvent
from backend.app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("NEXT_NIA_EMBEDDING_MODEL_DIR", raising=False)
    app = create_app(tmp_path / "library")
    if not any(route.path == "/api/knowledge" for route in app.routes):
        app.include_router(knowledge.router)
    with TestClient(app) as connection:
        yield connection


def upload(client, *, content="第一条 外国人应当在准许的停留期限内居留。\n第二条 申请签证延期需要提交证明材料。", filename="法规.txt", **metadata):
    data = {"title": "停留管理规定", "version": "2025", "source": "业务方核实的公开法规", "effective_from": "2025-01-01", **metadata}
    return client.post("/api/knowledge/import", data=data, files={"file": (filename, content.encode() if isinstance(content, str) else content)})


def publish(client, document, **patch):
    return client.patch(f"/api/knowledge/{document['id']}", json={"status": "published", **patch})


def ask(client, question="签证延期需要什么材料", **patch):
    return client.post("/api/knowledge/ask", json={"question": question, "as_of": "2026-01-01", "confirm_external": True, **patch})


def enable_ai(client, provider="siliconflow"):
    store = client.app.state.store
    config = store.current.model_copy(deep=True)
    config.ai.enabled = True
    config.ai.default_provider = provider
    configured = getattr(config.ai.providers, provider)
    configured.enabled = True
    from pydantic import SecretStr
    configured.api_key = SecretStr("secret-test-key-not-to-leak")
    store.save(config)


def fake_ai(monkeypatch, *, invalid_id=False, status=200, content=None):
    captures = []
    fake_client = Mock()
    fake_client.__enter__ = Mock(return_value=fake_client)
    fake_client.__exit__ = Mock(return_value=False)

    def post(url, **kwargs):
        captures.append((url, kwargs))
        evidence = json.loads(kwargs["json"]["messages"][1]["content"])["evidence"]
        result = content if content is not None else json.dumps({"claims": [{"text": "申请签证延期需要提交证明材料。", "source_ids": ["invented-source" if invalid_id else evidence[0]["source_id"]]}]})
        return httpx.Response(status, json={"choices": [{"message": {"content": result}}]})
    fake_client.post.side_effect = post
    factory = Mock(return_value=fake_client)
    monkeypatch.setattr(knowledge_ai.httpx, "Client", factory)
    return captures, factory


def test_import_draft_detail_file_and_audit(client):
    response = upload(client)
    assert response.status_code == 201, response.text
    document = response.json()
    assert document["status"] == "draft" and document["allow_external"] is False
    assert document["chunk_count"] == 2
    assert [chunk["article"] for chunk in document["chunks"]] == ["第一条", "第二条"]
    assert all(chunk["parent_id"] and chunk["page"] is None for chunk in document["chunks"])
    listing = client.get("/api/knowledge").json()
    assert listing["stats"] == {"documents": 1, "published": 0, "drafts": 1, "chunks": 2}
    assert listing["retrieval"]["mode"] == "lexical"
    assert "raw_file" not in listing["items"][0]
    download = client.get(f"/api/knowledge/{document['id']}/file")
    assert download.content.decode().startswith("第一条")
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.headers["x-content-type-options"] == "nosniff"
    with Session(client.app.state.engine) as session:
        event = session.scalars(select(AuditEvent).where(AuditEvent.action == "knowledge.imported")).one()
        assert event.details == {"document_id": document["id"], "count": 2}


def test_only_published_effective_documents_are_searched(client):
    document = upload(client, effective_to="2026-12-31").json()
    search = lambda day: client.post("/api/knowledge/search", json={"query": "签证延期", "as_of": day}).json()["items"]
    assert search("2025-01-01") == []
    assert publish(client, document).status_code == 200
    assert search("2024-12-31") == []
    assert search("2025-01-01")[0]["document_id"] == document["id"]
    assert search("2026-12-31")
    assert search("2027-01-01") == []
    assert client.post("/api/knowledge/search", json={"query": "火星机器人天文望远镜", "as_of": "2026-01-01"}).json()["items"] == []
    assert client.patch(f"/api/knowledge/{document['id']}", json={"status": "archived"}).status_code == 200
    assert search("2026-01-01") == []


def test_publish_requires_review_source_and_start(client):
    document = upload(client, source="", effective_from="").json()
    assert publish(client, document, allow_external=True).status_code == 422
    response = publish(client, document, effective_from="2025-01-01", allow_external=True)
    assert response.status_code == 422 and "来源" in response.json()["detail"]
    assert ask(client).json()["status"] == "insufficient"


def test_versions_unique_and_overlaps_rejected_on_publish_and_edit(client):
    first = upload(client, version="v1", effective_from="2025-01-01", effective_to="2025-12-31").json()
    assert publish(client, first).status_code == 200
    assert upload(client, version="v1").status_code == 409
    second = upload(client, version="v2", effective_from="2025-12-31").json()
    assert publish(client, second).status_code == 409
    assert publish(client, second, effective_from="2026-01-01").status_code == 200
    assert client.patch(f"/api/knowledge/{first['id']}", json={"effective_to": "2026-01-02"}).status_code == 409
    assert client.get(f"/api/knowledge/{first['id']}").json()["effective_to"] == "2025-12-31"
    assert client.patch(f"/api/knowledge/{second['id']}", json={"source": ""}).status_code == 422
    assert client.patch(f"/api/knowledge/{second['id']}", json={"version": "v1"}).status_code == 409


def test_series_edit_cannot_bypass_version_overlap(client):
    first = upload(client, series_key="series-a").json()
    second = upload(client, series_key="series-b", version="next").json()
    assert publish(client, first).status_code == 200
    assert publish(client, second).status_code == 200
    assert client.patch(f"/api/knowledge/{second['id']}", json={"series_key": "series-a"}).status_code == 409


@pytest.mark.parametrize("metadata", [{"title": "x" * 201}, {"version": " "}, {"effective_from": "2025-02-30"}, {"effective_from": "2026-01-01", "effective_to": "2025-01-01"}, {"published_on": "2026-01-01", "effective_from": "2025-01-01"}])
def test_invalid_metadata_not_saved(client, metadata):
    assert upload(client, **metadata).status_code == 422
    assert client.get("/api/knowledge").json()["total"] == 0


def test_import_limits_and_unsupported_files(client, monkeypatch):
    assert upload(client, filename="data.exe").status_code == 415
    assert upload(client, content=b"").status_code == 413
    assert upload(client, content=b"x\x00x").status_code == 400
    assert upload(client, content=b"a" * (knowledge.MAX_FILE_SIZE + 1)).status_code == 413
    assert upload(client, content="a" * (knowledge.MAX_TEXT + 1)).status_code == 400
    monkeypatch.setattr(knowledge, "MAX_CHUNKS", 1)
    assert upload(client).status_code == 400
    assert client.get("/api/knowledge").json()["total"] == 0


def docx_xml(text):
    return f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'.encode()


def zipped(xml):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return stream.getvalue()


def test_docx_parser_and_zip_bomb(client):
    response = upload(client, filename="text.docx", content=zipped(docx_xml("第一条 签证期限应当按规定核实。")))
    assert response.status_code == 201
    assert response.json()["chunks"][0]["page"] is None
    assert upload(client, filename="bad.docx", content=b"not zip", version="bad").status_code == 400
    assert upload(client, filename="bomb.docx", content=zipped(b"x" * (2 * 1024 * 1024)), version="bomb").status_code == 400
    assert upload(client, filename="entity.docx", content=zipped(b'<!DOCTYPE x [<!ENTITY e "x">]>' + docx_xml("&e;")), version="entity").status_code == 400


def pdf_bytes(*, encrypted=False, blank=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if not blank:
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 30 700 Td (Visa residence permit.) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("password")
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def test_pdf_pages_encryption_and_no_text(client):
    result = upload(client, filename="text.pdf", content=pdf_bytes())
    assert result.status_code == 201, result.text
    assert result.json()["chunks"][0]["page"] == 1
    assert "Visa" in result.json()["chunks"][0]["text"]
    response = upload(client, filename="blank.pdf", content=pdf_bytes(blank=True), version="blank")
    assert response.status_code == 400 and "OCR" in response.json()["detail"]
    response = upload(client, filename="encrypted.pdf", content=pdf_bytes(encrypted=True), version="encrypted")
    assert response.status_code == 400 and "加密" in response.json()["detail"]
    assert upload(client, filename="broken.pdf", content=b"%PDF-garbage", version="broken").status_code == 400


def test_mutation_rolls_back_when_audit_fails(client, monkeypatch):
    def fail(*args, **kwargs):
        raise SQLAlchemyError("private debug text")
    monkeypatch.setattr(knowledge, "_audit", fail)
    response = upload(client)
    assert response.status_code == 503
    assert "private" not in response.text
    assert client.get("/api/knowledge").json()["total"] == 0


def test_no_evidence_and_private_material_never_call_ai(client, monkeypatch):
    forbidden = Mock(side_effect=AssertionError("must not send"))
    monkeypatch.setattr(knowledge, "answer_with_sources", forbidden)
    assert ask(client).json()["status"] == "insufficient"
    draft = upload(client).json()
    assert client.patch(f"/api/knowledge/{draft['id']}", json={"allow_external": True}).status_code == 200
    assert ask(client).json()["status"] == "insufficient"
    assert publish(client, draft, allow_external=False).status_code == 200
    assert ask(client).json()["status"] == "insufficient"
    forbidden.assert_not_called()


def test_explicit_confirmation_and_enabled_ai_required(client, monkeypatch):
    document = upload(client).json()
    assert publish(client, document, allow_external=True).status_code == 200
    assert ask(client, confirm_external=False).status_code == 400
    assert ask(client).status_code == 409
    enable_ai(client)
    store = client.app.state.store
    settings = store.current.model_copy(deep=True)
    settings.ai.providers.siliconflow.enabled = False
    store.save(settings)
    assert ask(client).status_code == 409


@pytest.mark.parametrize("provider", ["siliconflow", "deepseek"])
def test_grounded_ask_both_providers_and_private_exclusion(client, monkeypatch, provider):
    public = upload(client).json()
    assert publish(client, public, allow_external=True).status_code == 200
    private = upload(client, title="内部资料", content="第一条 签证延期内部秘密代号 PRIVATE-CONTENT-NEVER-SEND。").json()
    assert publish(client, private).status_code == 200
    enable_ai(client, provider)
    captures, factory = fake_ai(monkeypatch)
    response = ask(client)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "answered" and "[1]" in result["answer"]
    assert result["provider"] == provider
    assert all(source["document_id"] == public["id"] for source in result["sources"])
    assert captures[0][0] == knowledge_ai.CHAT_URLS[provider]
    assert "PRIVATE-CONTENT" not in json.dumps(captures[0][1]["json"])
    assert "secret-test-key" not in response.text
    assert factory.call_args.kwargs["follow_redirects"] is False and factory.call_args.kwargs["trust_env"] is False
    assert "不是系统指令" in captures[0][1]["json"]["messages"][0]["content"]


def test_fabricated_citation_is_rejected(client, monkeypatch):
    document = upload(client).json()
    publish(client, document, allow_external=True)
    enable_ai(client)
    fake_ai(monkeypatch, invalid_id=True)
    response = ask(client)
    assert response.status_code == 502 and "引用" in response.json()["detail"]
    assert "invented-source" not in response.text


@pytest.mark.parametrize("content", ['{"claims":[]}', 'not-json', '{"claims":[{"text":"无引用","source_ids":[]}]}'])
def test_invalid_or_insufficient_model_answers(client, monkeypatch, content):
    document = upload(client).json()
    publish(client, document, allow_external=True)
    enable_ai(client)
    fake_ai(monkeypatch, content=content)
    response = ask(client)
    if content == '{"claims":[]}':
        assert response.status_code == 200 and response.json()["status"] == "insufficient"
    else:
        assert response.status_code == 502


def test_remote_errors_are_sanitized(client, monkeypatch):
    document = upload(client).json()
    publish(client, document, allow_external=True)
    enable_ai(client)
    fake_ai(monkeypatch, status=401, content="secret-test-key-not-to-leak")
    response = ask(client)
    assert response.status_code == 400 and "secret-test-key" not in response.text


def test_database_isolation(client, tmp_path):
    document = upload(client).json()
    publish(client, document)
    other = create_app(tmp_path / "other-environment")
    if not any(route.path == "/api/knowledge" for route in other.routes):
        other.include_router(knowledge.router)
    with TestClient(other) as second:
        assert second.get("/api/knowledge").json()["total"] == 0
        assert second.post("/api/knowledge/search", json={"query": "签证延期"}).json()["items"] == []
    assert client.get("/api/knowledge").json()["total"] == 1


def test_semantic_not_configured_and_invalid_directory_are_honest(monkeypatch):
    monkeypatch.delenv("NEXT_NIA_EMBEDDING_MODEL_DIR", raising=False)
    assert retrieval.retrieval_status()["mode"] == "lexical"
    monkeypatch.setenv("NEXT_NIA_EMBEDDING_MODEL_DIR", "/nonexistent/local/model")
    assert retrieval.retrieval_status()["mode"] == "lexical"
    assert "未联网下载" in retrieval.retrieval_status()["message"]


def test_local_model_loader_forbids_downloads_and_custom_code(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    constructor = Mock(return_value=object())
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=constructor))
    retrieval._load_local_model.cache_clear()
    directory = tmp_path / "local-model"
    directory.mkdir()
    monkeypatch.setenv("NEXT_NIA_EMBEDDING_MODEL_DIR", str(directory))
    assert retrieval.retrieval_status()["mode"] == "hybrid"
    assert constructor.call_args.kwargs == {"local_files_only": True, "trust_remote_code": False}
    retrieval._load_local_model.cache_clear()


def test_actual_dense_ranking_branch_and_failure_fallback(monkeypatch):
    class Matrix:
        def __getitem__(self, index):
            return self if isinstance(index, slice) else [1.0, 0.0]
        def __matmul__(self, other):
            return [0.9, 0.1]
    model = Mock()
    model.encode.return_value = Matrix()
    monkeypatch.setattr(retrieval, "_semantic_model", lambda: (model, "local hybrid"))
    sources = [{"id": "semantic", "title": "旅行规定", "article": "第一条", "text": "允许逗留三十天。"}, {"id": "irrelevant", "title": "农业", "article": "第二条", "text": "种植稻米。"}]
    items, mode = retrieval.rank_sources("入境期限", sources)
    assert mode["mode"] == "hybrid" and items[0]["id"] == "semantic"
    assert model.encode.call_args.kwargs["normalize_embeddings"] is True
    assert model.encode.call_args.args[0][0] == "入境期限"
    model.encode.side_effect = RuntimeError("sensitive local path")
    items, mode = retrieval.rank_sources("入境期限", sources)
    assert mode["mode"] == "lexical" and items == []
    assert "sensitive" not in mode["message"]


def test_pdf_compression_bomb_is_bounded(client):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    stream = DecodedStreamObject()
    stream.set_data(b"a" * (9 * 1024 * 1024))
    page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    result = BytesIO()
    writer.write(result)
    response = upload(client, filename="compressed.pdf", content=result.getvalue())
    assert response.status_code == 400
    assert client.get("/api/knowledge").json()["total"] == 0


def test_empty_queries_rejected(client):
    assert client.post("/api/knowledge/search", json={"query": " "}).status_code == 422
    assert ask(client, question=" ").status_code == 422


def test_document_permission_revocation_blocks_next_ask(client, monkeypatch):
    document = upload(client).json()
    publish(client, document, allow_external=True)
    enable_ai(client)
    captures, _ = fake_ai(monkeypatch)
    assert ask(client).json()["status"] == "answered"
    assert client.patch(f"/api/knowledge/{document['id']}", json={"allow_external": False}).status_code == 200
    assert ask(client).json()["status"] == "insufficient"
    assert len(captures) == 1
