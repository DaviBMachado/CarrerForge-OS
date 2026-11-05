from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.app.services import browser_manager
from backend.main import app


@pytest.fixture
def temp_session_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    session_dir = tmp_path / ".browser_session"
    monkeypatch.setattr(browser_manager, "SESSION_DIR", session_dir)
    monkeypatch.setattr(browser_manager, "STATE_FILE", session_dir / "state.json")
    return session_dir


@pytest.fixture
def client(temp_session_dir: Path):
    with TestClient(app) as test_client:
        yield test_client


def test_session_status_disconnected_by_default(client: TestClient):
    response = client.get("/api/scraper/session/status")
    assert response.status_code == 200
    data = response.json()
    assert data["authenticated"] is False
    assert data["linkedin"] is False
    assert data["gupy"] is False


def test_session_status_connected_when_cookies_present(client: TestClient, temp_session_dir: Path):
    temp_session_dir.mkdir(parents=True, exist_ok=True)
    state_file = temp_session_dir / "state.json"
    state_file.write_text(
        '{"cookies": ['
        '{"name": "li_at", "domain": ".www.linkedin.com", "value": "token123"},'
        '{"name": "gupy_session", "domain": ".gupy.io", "value": "gupytoken"}'
        "]}",
        encoding="utf-8",
    )

    response = client.get("/api/scraper/session/status")
    assert response.status_code == 200
    data = response.json()
    assert data["authenticated"] is True
    assert data["linkedin"] is True
    assert data["gupy"] is True


def test_session_login_launches_visible_browser(client: TestClient, temp_session_dir: Path):
    mock_page_1 = AsyncMock()
    mock_page_2 = AsyncMock()
    mock_context = AsyncMock()
    mock_context.pages = [mock_page_1]
    mock_context.new_page = AsyncMock(return_value=mock_page_2)
    mock_context.storage_state = AsyncMock(
        return_value={
            "cookies": [
                {"name": "li_at", "domain": ".linkedin.com", "value": "abc"},
            ]
        }
    )

    mock_pw = MagicMock()
    mock_pw.chromium.launch_persistent_context = AsyncMock(return_value=mock_context)
    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__.return_value = mock_pw

    with patch(
        "backend.app.services.browser_manager.async_playwright",
        return_value=mock_pw_cm,
    ):
        response = client.post("/api/scraper/session/login")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("launched", "authenticated")
    mock_pw.chromium.launch_persistent_context.assert_called_once()
    call_kwargs = mock_pw.chromium.launch_persistent_context.call_args.kwargs
    assert call_kwargs["headless"] is False
    assert Path(mock_pw.chromium.launch_persistent_context.call_args.args[0]) == temp_session_dir
    mock_page_1.goto.assert_called_once_with(browser_manager.LINKEDIN_LOGIN_URL)
    mock_page_2.goto.assert_called_once_with(browser_manager.GUPY_LOGIN_URL)


@pytest.mark.anyio
async def test_get_authenticated_context_creates_persistent_dir(temp_session_dir: Path):
    mock_context = AsyncMock()
    mock_pw = MagicMock()
    mock_pw.chromium.launch_persistent_context = AsyncMock(return_value=mock_context)
    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__.return_value = mock_pw

    with patch(
        "backend.app.services.browser_manager.async_playwright",
        return_value=mock_pw_cm,
    ):
        async with browser_manager.get_authenticated_context(headless=True) as ctx:
            assert ctx is mock_context
            assert temp_session_dir.exists()

    mock_context.close.assert_awaited_once()
