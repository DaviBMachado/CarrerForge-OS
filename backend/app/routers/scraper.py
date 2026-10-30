from typing import Any

from fastapi import APIRouter

from backend.app.services.browser_manager import get_session_status, launch_login_session

router = APIRouter(prefix="/scraper", tags=["scraper"])


@router.get("/session/status")
def session_status() -> dict[str, Any]:
    return get_session_status()


@router.post("/session/login")
async def session_login() -> dict[str, Any]:
    return await launch_login_session()
