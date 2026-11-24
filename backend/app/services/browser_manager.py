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
GUPY_LOGIN_URL = "https://login.gupy.io/candidates/signin"

PLATFORM_SERVICES: dict[str, dict[str, Any]] = {
    "linkedin": {
        "label": "LinkedIn",
        "login_url": LINKEDIN_LOGIN_URL,
        "domain": "linkedin.com",
        "cookie_names": {"li_at"},
    },
    "gupy": {
        "label": "Gupy",
        "login_url": GUPY_LOGIN_URL,
        "domain": "gupy.io",
        "cookie_names": {
            "gupy_session",
            "_gupy_session",
            "auth_token",
            "jwt",
            "access_token",
            "session_token",
            "socialprovider",
            "last_candidate_auth_subdomain",
        },
        "cookie_patterns": (
            "beamer_user_id",
            "candidate_auth",
            "socialprovider",
            "gupy_session",
        ),
    },
}

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_active_pw_cm: Any = None
_active_context: Any = None
_active_platform: str | None = None
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


def _merge_and_save_state(new_state: dict[str, Any]) -> None:
    ensure_session_dir()
    existing_cookies: list[dict[str, Any]] = []
    if STATE_FILE.is_file():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            existing_cookies = data.get("cookies", [])
        except Exception:
            pass

    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for c in existing_cookies:
        key = (str(c.get("domain", "")), str(c.get("name", "")), str(c.get("path", "/")))
        merged[key] = c
    for c in new_state.get("cookies", []):
        key = (str(c.get("domain", "")), str(c.get("name", "")), str(c.get("path", "/")))
        # Do not overwrite valid non-empty cookie value with an empty string
        if key in merged and merged[key].get("value") and not c.get("value"):
            continue
        merged[key] = c

    combined_state = {
        "cookies": list(merged.values()),
        "origins": new_state.get("origins", []),
    }
    _save_state_data(combined_state)


@asynccontextmanager
async def get_authenticated_context(headless: bool = True) -> AsyncGenerator[Any, None]:
    await close_interactive_session()
    session_path = ensure_session_dir()
    async with _session_lock:
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
    global _active_pw_cm, _active_context, _active_platform
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
        _active_platform = None
    return {"status": "closed"}


async def open_interactive_session(platform: str | None = None) -> dict[str, Any]:
    global _active_pw_cm, _active_context, _active_platform
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
            target_keys = (
                [platform]
                if platform and platform in PLATFORM_SERVICES
                else list(PLATFORM_SERVICES.keys())
            )
            for idx, p_key in enumerate(target_keys):
                svc = PLATFORM_SERVICES[p_key]
                if idx == 0:
                    page = context.pages[0] if context.pages else await context.new_page()
                else:
                    page = await context.new_page()
                await page.goto(svc["login_url"])

            _active_pw_cm = pw_cm
            _active_context = context
            _active_platform = platform if platform in PLATFORM_SERVICES else None
        except Exception:
            try:
                await pw_cm.__aexit__(None, None, None)
            except Exception:
                pass
            raise
    return {"status": "opened", "platform": platform or "all"}


async def launch_login_session() -> dict[str, Any]:
    await open_interactive_session()
    status = get_session_status()
    return {"status": "opened", **status}


def check_platform_auth(cookies: list[dict[str, Any]], platform: str) -> bool:
    svc = PLATFORM_SERVICES.get(platform)
    if not svc:
        return False
    domain = svc["domain"]
    cookie_names = {n.lower() for n in svc["cookie_names"]}
    patterns = svc.get("cookie_patterns", ())
    for c in cookies:
        d = str(c.get("domain", ""))
        n = str(c.get("name", ""))
        v = str(c.get("value", ""))
        if not v:
            continue
        if domain in d:
            nl = n.lower()
            if nl in cookie_names or any(p in nl for p in patterns):
                return True
    return False


def _check_sqlite_platform_cookies(cookie_db: Path, platform: str) -> bool:
    if not cookie_db.is_file():
        return False
    svc = PLATFORM_SERVICES.get(platform)
    if not svc:
        return False
    domain = svc["domain"]
    cookie_names = {n.lower() for n in svc["cookie_names"]}
    patterns = svc.get("cookie_patterns", ())
    try:
        conn = sqlite3.connect(f"file:{cookie_db.as_posix()}?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            cur.execute("SELECT host_key, name FROM cookies")
            rows = cur.fetchall()
        finally:
            conn.close()
        for h, n in rows:
            if domain in (h or ""):
                nl = (n or "").lower()
                if nl in cookie_names or any(p in nl for p in patterns):
                    return True
        return False
    except Exception:
        return False


def _check_sqlite_cookies(cookie_db: Path) -> tuple[bool, bool]:
    has_li = _check_sqlite_platform_cookies(cookie_db, "linkedin")
    has_gupy = _check_sqlite_platform_cookies(cookie_db, "gupy")
    return has_li, has_gupy


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
                    if value and str(value).strip():
                        cookies.append({"name": name, "domain": host_key, "value": str(value)})
            finally:
                conn.close()
        except Exception:
            pass
    if cookies:
        _merge_and_save_state({"cookies": cookies, "origins": []})


async def verify_interactive_session(platform: str | None = None) -> dict[str, Any]:
    global _active_context, _active_platform
    target_platform = platform or _active_platform

    if _active_context is not None:
        try:
            state = await _active_context.storage_state()
            if isinstance(state, dict):
                _merge_and_save_state(state)
        except Exception:
            pass

    _dump_sqlite_to_state()
    status = get_session_status()
    is_valid = (
        status["platforms"].get(target_platform, False)
        if target_platform
        else status["authenticated"]
    )

    if is_valid:
        await close_interactive_session()
        return {"status": "authenticated", "verified_platform": target_platform, **status}

    return {"status": "unauthenticated", "verified_platform": target_platform, **status}


def get_session_status() -> dict[str, Any]:
    platforms_status: dict[str, bool] = {k: False for k in PLATFORM_SERVICES}
    all_cookies: list[dict[str, Any]] = []

    if STATE_FILE.is_file():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            all_cookies = data.get("cookies", [])
        except Exception:
            pass

    for p in PLATFORM_SERVICES:
        if check_platform_auth(all_cookies, p):
            platforms_status[p] = True

    if not all(platforms_status.values()) and SESSION_DIR.is_dir():
        for rel in ("Default/Network/Cookies", "Default/Cookies", "Network/Cookies"):
            db_path = SESSION_DIR / rel
            for p, auth in platforms_status.items():
                if not auth and _check_sqlite_platform_cookies(db_path, p):
                    platforms_status[p] = True

    is_authenticated = any(platforms_status.values())
    return {
        "authenticated": is_authenticated,
        "linkedin": platforms_status.get("linkedin", False),
        "gupy": platforms_status.get("gupy", False),
        "platforms": platforms_status,
        "active_platform": _active_platform,
        "session_dir": str(SESSION_DIR),
        "browser_open": _active_context is not None,
    }
