import json
import os
import sqlite3

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from backend.app import ai, main
from backend.app.config import ConfigurationError, SettingsStore
from backend.app.schemas import Settings


@pytest.fixture
def client(tmp_path):
    with TestClient(main.create_app(tmp_path)) as instance:
        yield instance


def payload(client):
    settings = client.get("/api/settings").json()["settings"]
    settings["environment"]["postgres"].pop("password_configured")
    for provider in settings["ai"]["providers"].values():
        provider.pop("api_key_configured")
    return settings


def test_first_start_creates_private_local_configuration(client, tmp_path):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["active_environment"] == "test"
    assert health.json()["database_engine"] == "SQLite"
    assert health.json()["pending_restart"] is False
    assert (tmp_path / "settings.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "test.sqlite3").stat().st_mode & 0o777 == 0o600
    assert health.headers["cache-control"] == "no-store"
    public = client.get("/api/settings").json()["settings"]
    assert public["ai"]["providers"]["deepseek"]["chat_model"] == "deepseek-flash"


def test_secrets_are_saved_retained_and_cleared_without_response_or_audit_leak(client, tmp_path):
    body = payload(client)
    body["environment"]["postgres"]["password"] = "database-secret-123"
    body["ai"]["providers"]["siliconflow"]["api_key"] = "sf-secret-123"
    body["ai"]["providers"]["deepseek"]["api_key"] = "ds-secret-123"
    saved = client.put("/api/settings", json=body)
    assert saved.status_code == 200
    for secret in ("database-secret-123", "sf-secret-123", "ds-secret-123"):
        assert secret not in saved.text
        assert secret not in client.get("/api/settings").text
    stored = json.loads((tmp_path / "settings.json").read_text())["settings"]
    assert stored["ai"]["providers"]["siliconflow"]["api_key"] == "sf-secret-123"
    body = payload(client)
    body["ai"]["providers"]["siliconflow"]["api_key"] = ""
    body["environment"]["postgres"]["password"] = ""
    body["general"]["system_name"] = "新的系统名称"
    assert client.put("/api/settings", json=body).status_code == 200
    assert client.app.state.store.current.ai.providers.siliconflow.api_key.get_secret_value() == "sf-secret-123"
    assert client.app.state.store.current.environment.postgres.password.get_secret_value() == "database-secret-123"
    body["ai"]["providers"]["siliconflow"]["clear_api_key"] = True
    body["environment"]["postgres"]["clear_password"] = True
    cleared = client.put("/api/settings", json=body).json()
    assert cleared["settings"]["ai"]["providers"]["siliconflow"]["api_key_configured"] is False
    assert cleared["settings"]["environment"]["postgres"]["password_configured"] is False
    with sqlite3.connect(tmp_path / "test.sqlite3") as connection:
        audit = str(connection.execute("select action,details from audit_events").fetchall())
        assert "settings.updated" in audit
        for secret in ("database-secret-123", "sf-secret-123", "ds-secret-123"):
            assert secret not in audit


def test_configuration_survives_restart(tmp_path):
    with TestClient(main.create_app(tmp_path)) as client:
        body = payload(client)
        body["general"]["organization"] = "测试业务中心"
        body["ai"]["providers"]["deepseek"]["api_key"] = "persisted-key"
        assert client.put("/api/settings", json=body).status_code == 200
    with TestClient(main.create_app(tmp_path)) as client:
        settings = client.get("/api/settings").json()["settings"]
        assert settings["general"]["organization"] == "测试业务中心"
        assert settings["ai"]["providers"]["deepseek"]["api_key_configured"] is True
        assert "persisted-key" not in client.get("/api/settings").text


@pytest.mark.parametrize("bad_url", ["https://example.invalid/v1", "http://127.0.0.1:9000", "https://api.siliconflow.cn.evil.invalid/v1"])
def test_provider_urls_cannot_be_overridden(client, bad_url):
    body = payload(client)
    body["ai"]["providers"]["siliconflow"]["base_url"] = bad_url
    body["ai"]["providers"]["siliconflow"]["api_key"] = "must-not-leak"
    response = client.put("/api/settings", json=body)
    assert response.status_code == 422
    assert "must-not-leak" not in response.text
    assert bad_url not in response.text


def test_validation_never_echoes_secret_input(client):
    body = payload(client)
    body["environment"]["postgres"]["port"] = "private-password-as-invalid-port"
    response = client.put("/api/settings", json=body)
    assert response.status_code == 422
    assert "private-password" not in response.text
    response = client.post("/api/settings/test-ai", json={"provider": "unknown", "api_key": "private-key"})
    assert response.status_code == 422
    assert "private-key" not in response.text


def test_invalid_production_database_does_not_save_or_change_runtime(client, tmp_path, monkeypatch):
    before = (tmp_path / "settings.json").read_bytes()
    body = payload(client)
    body["environment"]["mode"] = "production"
    body["environment"]["postgres"]["password"] = "private-password"

    def failure(config):
        raise SQLAlchemyError("private-password should never appear")

    monkeypatch.setattr(main, "probe_postgres", failure)
    response = client.put("/api/settings", json=body)
    assert response.status_code == 400
    assert "private-password" not in response.text
    assert before == (tmp_path / "settings.json").read_bytes()
    assert client.get("/api/health").json()["active_environment"] == "test"


def test_environment_switch_validates_then_waits_for_restart(tmp_path, monkeypatch):
    probes = []
    monkeypatch.setattr(main, "probe_postgres", lambda config: probes.append(config) or 8)
    with TestClient(main.create_app(tmp_path)) as client:
        body = payload(client)
        body["environment"]["mode"] = "production"
        response = client.put("/api/settings", json=body)
        assert response.status_code == 200
        assert len(probes) == 1
        assert response.json()["runtime"]["active_environment"] == "test"
        assert response.json()["runtime"]["pending_restart"] is True
        assert client.get("/api/dashboard").json()["demo"] is False
    # Exercise production app behavior with an isolated local test double, not a
    # real PostgreSQL instance. No test rows are copied to the production engine.
    production_file = tmp_path / "production-double.sqlite3"
    monkeypatch.setattr(main, "application_engine", lambda settings, directory: create_engine(f"sqlite:///{production_file}", connect_args={"check_same_thread": False}))
    with TestClient(main.create_app(tmp_path)) as client:
        health = client.get("/api/health").json()
        assert health["active_environment"] == "production"
        assert health["database_engine"] == "PostgreSQL"
        assert health["pending_restart"] is False
        dashboard = client.get("/api/dashboard").json()
        assert dashboard["demo"] is False
        assert all(value == 0 for value in dashboard["stats"].values())
        assert dashboard["trend"] == dashboard["alerts"] == dashboard["activities"] == []
        body = payload(client)
        body["environment"]["mode"] = "test"
        assert client.put("/api/settings", json=body).json()["runtime"]["pending_restart"] is True
        assert client.get("/api/dashboard").json()["demo"] is False
    with sqlite3.connect(production_file) as connection:
        assert {row[0] for row in connection.execute("select name from sqlite_master where type='table'")} == {"audit_events", "people", "person_stays", "knowledge_documents", "knowledge_sections", "knowledge_chunks", "alert_rules", "stay_alert_facts", "alerts", "alert_history", "document_templates", "document_drafts", "document_history", "record_sessions", "record_audio", "record_segments", "record_versions", "record_transcription_attempts", "record_analyses"}


def test_database_test_uses_unsaved_fields_preserves_password_and_does_not_save(client, tmp_path, monkeypatch):
    body = payload(client)
    body["environment"]["postgres"]["password"] = "saved-db-pass"
    assert client.put("/api/settings", json=body).status_code == 200
    before = (tmp_path / "settings.json").read_bytes()
    calls = []
    monkeypatch.setattr(main, "probe_postgres", lambda config: calls.append(config) or 6)
    response = client.post("/api/settings/test-database", json={"host": "192.168.1.20", "database": "candidate", "password": ""})
    assert response.status_code == 200
    assert calls[0].database == "candidate"
    assert calls[0].password.get_secret_value() == "saved-db-pass"
    assert before == (tmp_path / "settings.json").read_bytes()


def mock_models(monkeypatch, handler):
    client_class = httpx.Client
    options = []

    def client_factory(**kwargs):
        options.append(kwargs)
        return client_class(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ai.httpx, "Client", client_factory)
    return options


@pytest.mark.parametrize("provider,model,url", [
    ("siliconflow", "Qwen/Qwen3-8B", "https://api.siliconflow.cn/v1/models"),
    ("deepseek", "deepseek-flash", "https://api.deepseek.com/models"),
])
def test_ai_check_only_requests_fixed_models_endpoint(client, monkeypatch, provider, model, url):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": model}]})

    options = mock_models(monkeypatch, handler)
    response = client.post("/api/settings/test-ai", json={"provider": provider, "api_key": "unit-test-key", "chat_model": model})
    assert response.status_code == 200
    assert response.json()["models"] == [model]
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == url
    assert requests[0].content == b""
    assert requests[0].headers["authorization"] == "Bearer unit-test-key"
    assert options[0]["follow_redirects"] is False
    assert options[0]["trust_env"] is False
    assert "unit-test-key" not in response.text


def test_ai_missing_key_does_not_contact_service(client, monkeypatch):
    def fail_if_called(**kwargs):
        pytest.fail("AI service must not be contacted without a key")

    monkeypatch.setattr(ai.httpx, "Client", fail_if_called)
    response = client.post("/api/settings/test-ai", json={"provider": "deepseek"})
    assert response.status_code == 400


@pytest.mark.parametrize("status,expected", [(401, 400), (403, 400), (429, 429), (500, 502), (302, 502)])
def test_ai_remote_errors_are_sanitized(client, monkeypatch, status, expected):
    mock_models(monkeypatch, lambda request: httpx.Response(status, text="secret-upstream-content", headers={"location": "https://evil.invalid"}))
    response = client.post("/api/settings/test-ai", json={"provider": "deepseek", "api_key": "private-api-key"})
    assert response.status_code == expected
    assert "private-api-key" not in response.text
    assert "secret-upstream-content" not in response.text
    assert "evil.invalid" not in response.text


def test_ai_invalid_model_is_not_success(client, monkeypatch):
    mock_models(monkeypatch, lambda request: httpx.Response(200, json={"data": [{"id": "different-model"}]}))
    response = client.post("/api/settings/test-ai", json={"provider": "deepseek", "api_key": "private-key"})
    assert response.status_code == 400


def test_model_list_cannot_echo_the_api_key(client, monkeypatch):
    mock_models(monkeypatch, lambda request: httpx.Response(200, json={"data": [{"id": "deepseek-flash"}, {"id": "private-key"}]}))
    response = client.post("/api/settings/test-ai", json={"provider": "deepseek", "api_key": "private-key"})
    assert response.status_code == 200
    assert response.json()["models"] == ["deepseek-flash"]
    assert "private-key" not in response.text


def test_api_key_format_rejected_without_echo(client):
    body = payload(client)
    body["ai"]["providers"]["deepseek"]["api_key"] = "private-key\nheader-injection"
    response = client.put("/api/settings", json=body)
    assert response.status_code == 422
    assert "private-key" not in response.text


def test_ai_timeout_is_reported_without_request_details(client, monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("private-key included in transport error")

    mock_models(monkeypatch, handler)
    response = client.post("/api/settings/test-ai", json={"provider": "deepseek", "api_key": "private-key"})
    assert response.status_code == 504
    assert "private-key" not in response.text


def test_cross_site_configuration_changes_are_rejected(client):
    body = payload(client)
    response = client.put("/api/settings", json=body, headers={"origin": "https://unrelated.invalid"})
    assert response.status_code == 403
    response = client.put("/api/settings", json=body, headers={"origin": "http://testserver"})
    assert response.status_code == 200


def test_invalid_settings_file_is_not_overwritten(tmp_path):
    path = tmp_path / "settings.json"
    content = '{"schema_version":1,"settings":{"private-key":'
    path.write_text(content)
    with pytest.raises(ConfigurationError) as failure:
        with TestClient(main.create_app(tmp_path)):
            pass
    assert "private-key" not in str(failure.value)
    assert path.read_text() == content


def test_failed_atomic_save_keeps_original_settings(client, tmp_path, monkeypatch):
    original = (tmp_path / "settings.json").read_bytes()

    def fail_replace(*args):
        raise OSError("sensitive file details")

    monkeypatch.setattr(os, "replace", fail_replace)
    body = payload(client)
    body["general"]["system_name"] = "不应保存"
    response = client.put("/api/settings", json=body)
    assert response.status_code == 500
    assert "sensitive file details" not in response.text
    assert (tmp_path / "settings.json").read_bytes() == original
    assert client.get("/api/settings").json()["settings"]["general"]["system_name"] != "不应保存"
    assert list(tmp_path.glob(".settings-*.tmp")) == []


def test_production_start_failure_does_not_fallback_to_demo(tmp_path, monkeypatch):
    settings = Settings()
    settings.environment.mode = "production"
    store = SettingsStore(tmp_path)
    store.save(settings)

    class BrokenEngine:
        def dispose(self):
            pass

    monkeypatch.setattr(main, "application_engine", lambda *args: BrokenEngine())

    def fail(*args):
        raise SQLAlchemyError("private-db-password")

    monkeypatch.setattr(main.Base.metadata, "create_all", fail)
    with pytest.raises(ConfigurationError) as error:
        with TestClient(main.create_app(tmp_path)):
            pass
    assert "private-db-password" not in str(error.value)
    assert not (tmp_path / "test.sqlite3").exists()
    assert store.load().environment.mode == "production"


def test_asr_model_is_public_configurable_and_preserved_for_older_clients(client):
    body = payload(client)
    assert body["ai"]["providers"]["siliconflow"]["asr_model"] == "FunAudioLLM/SenseVoiceSmall"
    body["ai"]["providers"]["siliconflow"]["asr_model"] = "  custom/speech-model  "
    response = client.put("/api/settings", json=body)
    assert response.status_code == 200
    assert response.json()["settings"]["ai"]["providers"]["siliconflow"]["asr_model"] == "custom/speech-model"
    body = payload(client)
    body["ai"]["providers"]["siliconflow"].pop("asr_model")
    response = client.put("/api/settings", json=body)
    assert response.status_code == 200
    assert response.json()["settings"]["ai"]["providers"]["siliconflow"]["asr_model"] == "custom/speech-model"
    assert "asr_model" not in response.json()["settings"]["ai"]["providers"]["deepseek"]


@pytest.mark.parametrize("value", ["", "  ", "speech\nmodel", "x" * 201])
def test_invalid_asr_model_never_replaces_saved_settings(client, value):
    body = payload(client)
    body["ai"]["providers"]["siliconflow"]["asr_model"] = value
    assert client.put("/api/settings", json=body).status_code == 422
    assert client.get("/api/settings").json()["settings"]["ai"]["providers"]["siliconflow"]["asr_model"] == "FunAudioLLM/SenseVoiceSmall"
