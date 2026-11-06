from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from backend.app.db import get_db
from backend.app.models import JobPosting
from backend.app.routers.profile import get_or_create_default_profile
from backend.app.services.browser_manager import (
    close_interactive_session,
    get_session_status,
    launch_login_session,
    open_interactive_session,
    verify_interactive_session,
)
from backend.app.services.scrapers.runner import (
    RUNS,
    start_scraper_run,
    stop_scraper_run,
    stream_run_events,
)

router = APIRouter(prefix="/scraper", tags=["scraper"])

DBSession = Annotated[Session, Depends(get_db)]


class ScraperStartRequest(BaseModel):
    query: str = "Python"
    location: str = ""
    platforms: list[str] = Field(default_factory=lambda: ["linkedin", "gupy"])
    remote: bool = False
    max_jobs: int = 25


class ScraperStopRequest(BaseModel):
    run_id: str | None = None


@router.get("/session/status")
def session_status() -> dict[str, Any]:
    return get_session_status()


class SessionActionRequest(BaseModel):
    platform: str | None = None


@router.post("/session/login")
async def session_login() -> dict[str, Any]:
    return await launch_login_session()


@router.post("/session/open")
async def session_open(
    payload: SessionActionRequest | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    target = (payload.platform if payload else None) or platform
    return await open_interactive_session(target)


@router.post("/session/verify")
async def session_verify(
    payload: SessionActionRequest | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    target = (payload.platform if payload else None) or platform
    res = await verify_interactive_session(target)
    is_valid = (
        res.get("platforms", {}).get(target, False) if target else res.get("authenticated", False)
    )
    if not is_valid:
        detail_msg = (
            f"Authentication not detected for {target}. "
            "Please complete login in the opened browser."
            if target
            else "Authentication not detected. Please complete login in the opened browser."
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail_msg,
        )
    return res


@router.post("/session/close")
async def session_close() -> dict[str, Any]:
    return await close_interactive_session()


@router.post("/start")
async def start_scraper(
    payload: ScraperStartRequest | None = None,
    query: str | None = None,
    location: str | None = None,
    remote: bool | None = None,
    max_jobs: int | None = None,
) -> dict[str, str]:
    req = payload or ScraperStartRequest()
    run = await start_scraper_run(
        query=query if query is not None else req.query,
        location=location if location is not None else req.location,
        platforms=req.platforms,
        remote=remote if remote is not None else req.remote,
        max_jobs=max_jobs if max_jobs is not None else req.max_jobs,
    )
    return {"run_id": run.run_id}


@router.get("/stream/{run_id}")
async def stream_scraper(run_id: str) -> StreamingResponse:
    if run_id not in RUNS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scraper run not found",
        )
    return StreamingResponse(
        stream_run_events(run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/stop")
async def stop_scraper(
    payload: ScraperStopRequest | None = None,
    run_id: str | None = None,
) -> dict[str, str]:
    target_id = run_id or (payload.run_id if payload else None)
    run = await stop_scraper_run(target_id)
    if not run:
        return {"status": "idle", "run_id": target_id or ""}
    return {"status": run.status, "run_id": run.run_id}


class JobStatusUpdateRequest(BaseModel):
    status: str


@router.get("/jobs", response_model=list[JobPosting])
def list_scraped_jobs(session: DBSession) -> list[JobPosting]:
    profile = get_or_create_default_profile(session)
    jobs = session.exec(
        select(JobPosting)
        .where(JobPosting.user_id == profile.user_id)
        .order_by(col(JobPosting.id).desc())
    ).all()
    return list(jobs)


@router.patch("/jobs/{job_id}/status", response_model=JobPosting)
def update_job_status(
    job_id: int,
    payload: JobStatusUpdateRequest,
    session: DBSession,
) -> JobPosting:
    profile = get_or_create_default_profile(session)
    job = session.get(JobPosting, job_id)
    if not job or job.user_id != profile.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job posting not found",
        )
    job.status = payload.status
    session.add(job)
    session.commit()
    session.refresh(job)
    return job
