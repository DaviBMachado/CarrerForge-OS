import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
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
    get_tailored_artifact,
    solve_screening_question,
    tailor_job_application,
)

router = APIRouter(prefix="/tailor", tags=["tailor"])

DBSession = Annotated[Session, Depends(get_db)]


def get_llm_orchestrator(session: DBSession, current_user: CurrentUser) -> LLMOrchestrator:
    assert current_user.id is not None
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


class ExpandSectionRequest(BaseModel):
    current_text: str = ""


class ExpandSectionResponse(BaseModel):
    expanded_text: str


@router.post("/expand-section", response_model=ExpandSectionResponse)
def expand_resume_section(
    payload: ExpandSectionRequest,
    session: DBSession,
    orchestrator: LLMClient,
    current_user: CurrentUser,
) -> ExpandSectionResponse:
    profile = get_or_create_user_profile(session, current_user)
    experiences = profile.experiences or []

    prof_summary = {
        "headline": profile.headline,
        "summary_base": profile.summary_base,
        "skills": profile.skills_json,
        "experiences": [
            {
                "company": e.company,
                "role": e.role,
                "location": e.location,
                "is_contract": getattr(e, "is_contract", False),
                "is_academic": getattr(e, "is_academic", False),
                "bullets": e.bullets_json or [],
            }
            for e in experiences
        ],
    }

    system_prompt = (
        "You are an expert resume developer. The user wants to expand their resume section with "
        "dense, highly detailed engineering achievements and metrics based strictly on their Master Profile.\n"
        "STRICT RULES:\n"
        "1. Impersonal style: NEVER use first-person ('I', 'me', 'my', 'eu') or third-person narration ('He led', 'She did', 'O candidato'). "
        "Start all bullets directly with high-impact past-tense action verbs (e.g. 'Architected...', 'Built...', 'Designed...', 'Desenvolveu...').\n"
        "2. General Introduction / Summary rule: In any general summary or introduction, do NOT name specific past companies. "
        "Focus on technical competencies, architecture paradigms, and problem domains.\n"
        "3. High depth and metrics: Generate 3 to 5 expanded bullet points with scope, problem solved, technologies applied, and quantified results.\n"
        "4. Strict Truthfulness: Do not invent companies, degrees, or metrics not grounded in the Master Profile.\n"
        "5. Return only raw markdown (e.g. '### [Section Title]\\n- [Bullet 1]\\n- [Bullet 2]...') without json wrapping."
    )

    prompt = (
        f"Master Profile data:\n{json.dumps(prof_summary, ensure_ascii=False, indent=2)}\n\n"
        f"Existing Resume Text:\n{payload.current_text}\n\n"
        "Generate a complementary high-impact expanded section (with a subheading and rich quantified bullets) to expand this resume."
    )

    try:
        raw_text = orchestrator.generate(
            prompt=prompt, system_prompt=system_prompt, temperature=0.2
        )
        clean_text = raw_text.strip()
        if clean_text.startswith("```"):
            clean_text = re.sub(r"^```[a-zA-Z]*\n?", "", clean_text)
            clean_text = re.sub(r"\n?```$", "", clean_text).strip()
        return ExpandSectionResponse(expanded_text=clean_text)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


@router.get("/{job_id}", response_model=TailoringResult)
def get_tailored_job(
    job_id: int,
    session: DBSession,
    current_user: CurrentUser,
) -> TailoringResult:
    job, profile = _get_user_job(session, job_id, current_user)
    result = get_tailored_artifact(session, job, profile)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tailored artifact not yet generated for this job",
        )
    return result


@router.post("/{job_id}", response_model=TailoringResult)
def run_tailor_job(
    job_id: int,
    session: DBSession,
    orchestrator: LLMClient,
    current_user: CurrentUser,
    mode: str = "ats",
) -> TailoringResult:
    if mode not in ("ats", "human"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query parameter 'mode' must be 'ats' or 'human'",
        )
    job, profile = _get_user_job(session, job_id, current_user)
    try:
        return tailor_job_application(session, job, profile, orchestrator=orchestrator, mode=mode)
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
