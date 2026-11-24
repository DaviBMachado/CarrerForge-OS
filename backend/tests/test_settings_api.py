import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.app.db import get_db
from backend.main import app


@pytest.fixture
def client():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(Engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(test_engine)

    def override_get_db():
        with Session(test_engine) as sess:
            yield sess

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_get_default_settings(client: TestClient):
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["active_provider"] == "gemini"
    assert data["gemini_api_key"] is None
    assert data["openai_api_key"] is None
    assert data["anthropic_api_key"] is None
    assert "ollama_base_url" in data
    assert data["scraper_headless"] is True


def test_save_and_read_settings_with_masking(client: TestClient):
    payload = {
        "active_provider": "openai",
        "openai_api_key": "sk-proj-1234567890abcdef",
        "gemini_api_key": "AIzaSySecretGeminiKey123",
        "ollama_base_url": "http://localhost:11434",
        "scraper_headless": False,
        "scraper_timeout": 45,
    }

    post_resp = client.post("/api/settings", json=payload)
    assert post_resp.status_code == 200
    post_data = post_resp.json()

    assert post_data["active_provider"] == "openai"
    assert post_data["scraper_headless"] is False
    assert post_data["scraper_timeout"] == 45
    # Verify masked return
    assert post_data["openai_api_key"] == "sk-...cdef"
    assert post_data["gemini_api_key"] == "AIz...y123"
    assert "1234567890" not in post_data["openai_api_key"]

    # Verify GET returns same masked indicators
    get_resp = client.get("/api/settings")
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["active_provider"] == "openai"
    assert get_data["openai_api_key"] == "sk-...cdef"
    assert get_data["gemini_api_key"] == "AIz...y123"


def test_preserve_secret_when_masked_indicator_sent(client: TestClient):
    # Initial save
    client.post(
        "/api/settings",
        json={"openai_api_key": "sk-real-super-secret-key-1234"},
    )

    # User submits form without touching the masked key (sends masked value back)
    update_resp = client.post(
        "/api/settings",
        json={
            "active_provider": "claude",
            "openai_api_key": "sk-...1234",
            "anthropic_api_key": "sk-ant-real-anthropic-key-9999",
        },
    )
    assert update_resp.status_code == 200
    data = update_resp.json()
    assert data["active_provider"] == "claude"
    assert data["openai_api_key"] == "sk-...1234"
    assert data["anthropic_api_key"] == "sk-...9999"


def test_clear_secret_with_empty_string(client: TestClient):
    client.post("/api/settings", json={"gemini_api_key": "AIzaInitialKey1234"})
    get_resp = client.get("/api/settings")
    assert get_resp.json()["gemini_api_key"] is not None

    clear_resp = client.post("/api/settings", json={"gemini_api_key": ""})
    assert clear_resp.status_code == 200
    assert clear_resp.json()["gemini_api_key"] is None


def test_invalid_provider_rejected(client: TestClient):
    response = client.post("/api/settings", json={"active_provider": "unknown_ai"})
    assert response.status_code == 422


def test_openrouter_call_fallback_on_400_response_format(monkeypatch):
    from typing import cast

    import httpx
    from sqlmodel import Session

    from backend.app.services.llm_orchestrator import LLMOrchestrator

    call_count = 0

    def mock_post(url, headers=None, json=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                400,
                json={
                    "error": {"message": "Provider does not support response_format: json_object"}
                },
                request=httpx.Request("POST", url),
            )
        assert isinstance(json, dict)
        assert "response_format" not in json
        assert "json" in json["messages"][0]["content"].lower()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"result": "ok"}'}}]},
            request=httpx.Request("POST", url),
        )

    client = httpx.Client()
    monkeypatch.setattr(client, "post", mock_post)
    orchestrator = LLMOrchestrator(session=cast(Session, None))
    res = orchestrator._call_openrouter(
        client=client,
        api_key="test-key",
        prompt="hello",
        system_prompt="system",
        json_mode=True,
        temperature=0.2,
    )
    assert res == '{"result": "ok"}'
    assert call_count == 2
