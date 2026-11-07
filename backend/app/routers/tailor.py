from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from backend.app.auth import CurrentUser
from backend.app.db import get_db
from backend.app.models import JobPosting, MasterProfile, User
from backend.app.routers.profile import get_or_create_user_profile
from backend.app.services.llm_orchestrator import (
    LLMConfigurationError,
    LLMOrchestrator,
    LLMProviderError,
)
from backend.app.services.tailoring_engine import (
    FactualGuardrailError,
    GupyRundown,
    SolveQuestionRequest,
    SolveQuestionResponse,
    TailoringResult,
    generate_gupy_rundown,
    solve_screening_question,
    tailor_job_application,
)

router = APIRouter(prefix="/tailor", tags=["tailor"])

DBSession = Annotated[Session, Depends(get_db)]


def get_llm_orchestrator(session: DBSession, current_user: CurrentUser) -> LLMOrchestrator:
    return LLMOrchestrator(session, user_id=current_user.id)


LLMClient = Annotated[LLMOrchestrator, Depends(get_llm_orchestrator)]


def _get_user_job(
    session: Session, job_id: int, current_user: User
) -> tuple[JobPosting, MasterProfile]:
    job = session.get(JobPosting, job_id)
    if not job or job.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job posting not found",
        )
    profile = get_or_create_user_profile(session, current_user)
    return job, profile


@router.post("/{job_id}", response_model=TailoringResult)
def run_tailor_job(
    job_id: int,
    session: DBSession,
    orchestrator: LLMClient,
    current_user: CurrentUser,
) -> TailoringResult:
    job, profile = _get_user_job(session, job_id, current_user)
    try:
        return tailor_job_application(session, job, profile, orchestrator=orchestrator)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FactualGuardrailError as exc:
        raise HTTPException(
            status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422),
            detail=str(exc),
        ) from exc
    except LLMProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/{job_id}/gupy-rundown", response_model=GupyRundown)
def get_gupy_rundown(job_id: int, session: DBSession, current_user: CurrentUser) -> GupyRundown:
    job, profile = _get_user_job(session, job_id, current_user)
    return generate_gupy_rundown(session, job, profile)


@router.post("/{job_id}/solve-question", response_model=SolveQuestionResponse)
def post_solve_question(
    job_id: int,
    payload: SolveQuestionRequest,
    session: DBSession,
    orchestrator: LLMClient,
    current_user: CurrentUser,
) -> SolveQuestionResponse:
    job, profile = _get_user_job(session, job_id, current_user)
    try:
        return solve_screening_question(
            session,
            job,
            profile,
            question=payload.question,
            orchestrator=orchestrator,
        )
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FactualGuardrailError as exc:
        raise HTTPException(
            status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422),
            detail=str(exc),
        ) from exc
    except LLMProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
