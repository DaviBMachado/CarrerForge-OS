import asyncio
import json
import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from backend.app.db import BACKEND_DIR

try:
    from playwright.async_api import async_playwright  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover

    def async_playwright() -> Any:
        raise RuntimeError("Playwright is not installed. Run 'pip install playwright'.")


SESSION_DIR = BACKEND_DIR / "data" / ".browser_session"
STATE_FILE = SESSION_DIR / "state.json"

LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
GUPY_LOGIN_URL = "https://login.gupy.io/"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_active_pw_cm: Any = None
_active_context: Any = None
_session_lock = asyncio.Lock()


def ensure_session_dir() -> Path:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return SESSION_DIR


def _save_state_data(state: dict[str, Any]) -> None:
    ensure_session_dir()
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    try:
        STATE_FILE.chmod(0o600)
    except Exception:
        pass


@asynccontextmanager
async def get_authenticated_context(headless: bool = True) -> AsyncGenerator[Any, None]:
    session_path = ensure_session_dir()
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(session_path),
            headless=headless,
            user_agent=DEFAULT_USER_AGENT,
            viewport={"width": 1280, "height": 800},
        )
        try:
            yield context
            state = await context.storage_state()
            if isinstance(state, dict):
                _save_state_data(state)
        finally:
            await context.close()


async def close_interactive_session() -> dict[str, str]:
    global _active_pw_cm, _active_context
    async with _session_lock:
        if _active_context is not None:
            try:
                await _active_context.close()
            except Exception:
                pass
            _active_context = None
        if _active_pw_cm is not None:
            try:
                await _active_pw_cm.__aexit__(None, None, None)
            except Exception:
                pass
            _active_pw_cm = None
    return {"status": "closed"}


async def open_interactive_session() -> dict[str, Any]:
    global _active_pw_cm, _active_context
    await close_interactive_session()
    session_path = ensure_session_dir()
    async with _session_lock:
        pw_cm = async_playwright()
        pw = await pw_cm.__aenter__()
        try:
            context = await pw.chromium.launch_persistent_context(
                str(session_path),
                headless=False,
                user_agent=DEFAULT_USER_AGENT,
                viewport={"width": 1280, "height": 800},
            )
            page_linkedin = context.pages[0] if context.pages else await context.new_page()
            await page_linkedin.goto(LINKEDIN_LOGIN_URL)
            page_gupy = await context.new_page()
            await page_gupy.goto(GUPY_LOGIN_URL)
            _active_pw_cm = pw_cm
            _active_context = context
        except Exception:
            try:
                await pw_cm.__aexit__(None, None, None)
            except Exception:
                pass
            raise
    return {"status": "opened"}


async def launch_login_session() -> dict[str, Any]:
    # shortcut: single-user local lock; concurrent login calls share the persistent profile dir
    session_path = ensure_session_dir()
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(session_path),
            headless=False,
            user_agent=DEFAULT_USER_AGENT,
            viewport={"width": 1280, "height": 800},
        )
        try:
            page_linkedin = context.pages[0] if context.pages else await context.new_page()
            await page_linkedin.goto(LINKEDIN_LOGIN_URL)
            page_gupy = await context.new_page()
            await page_gupy.goto(GUPY_LOGIN_URL)

            state = await context.storage_state()
            if isinstance(state, dict):
                _save_state_data(state)
        finally:
            await context.close()

    status = get_session_status()
    return {"status": "authenticated" if status["authenticated"] else "launched", **status}


def _check_sqlite_cookies(cookie_db: Path) -> tuple[bool, bool]:
    if not cookie_db.is_file():
        return False, False
    try:
        conn = sqlite3.connect(f"file:{cookie_db.as_posix()}?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            cur.execute("SELECT host_key, name FROM cookies")
            rows = cur.fetchall()
        finally:
            conn.close()
        has_li = any("linkedin.com" in (h or "") and n == "li_at" for h, n in rows)
        has_gupy = any("gupy.io" in (h or "") for h, _ in rows)
        return has_li, has_gupy
    except Exception:
        return False, False


def _dump_sqlite_to_state() -> None:
    cookies: list[dict[str, Any]] = []
    for rel in ("Default/Network/Cookies", "Default/Cookies", "Network/Cookies"):
        db_path = SESSION_DIR / rel
        if not db_path.is_file():
            continue
        try:
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            try:
                cur = conn.cursor()
                cur.execute("SELECT host_key, name, value FROM cookies")
                for host_key, name, value in cur.fetchall():
                    cookies.append({"name": name, "domain": host_key, "value": value or ""})
            finally:
                conn.close()
        except Exception:
            pass
    if cookies:
        _save_state_data({"cookies": cookies, "origins": []})


async def verify_interactive_session() -> dict[str, Any]:
    global _active_context
    if _active_context is not None:
        try:
            state = await _active_context.storage_state()
            if isinstance(state, dict):
                _save_state_data(state)
        except Exception:
            pass

    status = get_session_status()
    if status["authenticated"]:
        if not STATE_FILE.is_file():
            _dump_sqlite_to_state()
        await close_interactive_session()
        return {"status": "authenticated", **status}

    return {"status": "unauthenticated", **status}


def get_session_status() -> dict[str, Any]:
    has_linkedin = False
    has_gupy = False

    if STATE_FILE.is_file():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            cookies = data.get("cookies", [])
            for c in cookies:
                domain = str(c.get("domain", ""))
                name = str(c.get("name", ""))
                value = str(c.get("value", ""))
                if not value:
                    continue
                if "linkedin.com" in domain and (name == "li_at" or "session" in name.lower()):
                    has_linkedin = True
                if "gupy.io" in domain:
                    has_gupy = True
        except Exception:
            pass

    if not (has_linkedin and has_gupy) and SESSION_DIR.is_dir():
        for rel in ("Default/Network/Cookies", "Default/Cookies", "Network/Cookies"):
            li_db, gupy_db = _check_sqlite_cookies(SESSION_DIR / rel)
            has_linkedin = has_linkedin or li_db
            has_gupy = has_gupy or gupy_db

    return {
        "authenticated": has_linkedin or has_gupy,
        "linkedin": has_linkedin,
        "gupy": has_gupy,
        "session_dir": str(SESSION_DIR),
    }
