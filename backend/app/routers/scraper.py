from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from backend.app.auth import CurrentUser
from backend.app.db import get_db
from backend.app.models import Experience, JobPosting, MasterProfile
from backend.app.services.browser_manager import (
    close_interactive_session,
    get_session_status,
    launch_login_session,
    open_interactive_session,
    verify_interactive_session,
)
from backend.app.services.scrapers.deduplicator import upsert_scraped_jobs
from backend.app.services.scrapers.runner import (
    RUNS,
    start_scraper_run,
    stop_scraper_run,
    stream_run_events,
)
from backend.app.services.scrapers.single_extractor import scrape_job_by_url
from backend.app.services.tailoring_engine import extract_ats_keywords

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
    current_user: CurrentUser,
    payload: ScraperStartRequest | None = None,
    query: str | None = None,
    location: str | None = None,
    remote: bool | None = None,
    max_jobs: int | None = None,
) -> dict[str, str]:
    req = payload or ScraperStartRequest()
    assert current_user.id is not None
    run = await start_scraper_run(
        query=query if query is not None else req.query,
        location=location if location is not None else req.location,
        platforms=req.platforms,
        remote=remote if remote is not None else req.remote,
        max_jobs=max_jobs if max_jobs is not None else req.max_jobs,
        user_id=current_user.id,
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


class JobExtractUrlRequest(BaseModel):
    url: str = Field(..., min_length=5)


@router.post("/extract-url", response_model=JobPosting)
async def extract_job_from_url(
    payload: JobExtractUrlRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> JobPosting:
    assert current_user.id is not None
    try:
        scraped_job = await scrape_job_by_url(payload.url, headless=True)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Não foi possível extrair a vaga do link fornecido: {exc}. Você pode cadastrar a vaga manualmente no Estúdio de Personalização.",
        )

    saved_jobs = upsert_scraped_jobs(session, current_user.id, [scraped_job])
    if not saved_jobs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Falha ao salvar a vaga extraída.",
        )
    return saved_jobs[0]


class ManualJobCreateRequest(BaseModel):
    title: str = Field(..., min_length=1)
    company: str = Field(..., min_length=1)
    raw_description: str = Field(..., min_length=1)
    location: str | None = None
    remote_type: str | None = None
    url: str | None = None
    language: str = "en"


@router.post("/jobs", response_model=JobPosting, status_code=status.HTTP_201_CREATED)
def create_manual_job(
    payload: ManualJobCreateRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> JobPosting:
    assert current_user.id is not None
    title = payload.title.strip()
    company = payload.company.strip()
    raw_desc = payload.raw_description.strip()
    if not title or not company or not raw_desc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Title, company, and raw_description must not be empty.",
        )

    profile = session.exec(
        select(MasterProfile).where(MasterProfile.user_id == current_user.id)
    ).first()
    experiences = (
        list(session.exec(select(Experience).where(Experience.profile_id == profile.id)).all())
        if profile and profile.id
        else []
    )

    match_pct = None
    if profile:
        job_kws, _, pct = extract_ats_keywords(raw_desc, profile, experiences)
        match_pct = pct if job_kws else 0

    job = JobPosting(
        user_id=current_user.id,
        platform="manual",
        external_id=None,
        title=title,
        company=company,
        location=payload.location.strip() if payload.location and payload.location.strip() else None,
        remote_type=payload.remote_type.strip() if payload.remote_type and payload.remote_type.strip() else None,
        url=payload.url.strip() if payload.url and payload.url.strip() else None,
        raw_description=raw_desc,
        cleaned_text=raw_desc,
        language=payload.language or "en",
        status="discovered",
        match_percentage=match_pct,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


class JobStatusUpdateRequest(BaseModel):
    status: str


@router.get("/jobs", response_model=list[JobPosting])
def list_scraped_jobs(session: DBSession, current_user: CurrentUser) -> list[JobPosting]:
    jobs = session.exec(
        select(JobPosting)
        .where(JobPosting.user_id == current_user.id)
        .order_by(col(JobPosting.id).desc())
    ).all()
    return list(jobs)


@router.patch("/jobs/{job_id}/status", response_model=JobPosting)
def update_job_status(
    job_id: int,
    payload: JobStatusUpdateRequest,
    session: DBSession,
    current_user: CurrentUser,
) -> JobPosting:
    job = session.get(JobPosting, job_id)
    if not job or job.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job posting not found",
        )
    job.status = payload.status
    session.add(job)
    session.commit()
    session.refresh(job)
    return job
