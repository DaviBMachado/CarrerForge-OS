from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlmodel import Session, col, select

from backend.app.auth import CurrentUser, ensure_default_user
from backend.app.db import get_db
from backend.app.models import Experience, MasterProfile, User
from backend.app.schemas.profile import (
    BulletItem,
    ExperienceCreate,
    ExperienceRead,
    ExperienceUpdate,
    ProfileRead,
    ProfileUpdate,
)
from backend.app.services.document_parser import extract_text_from_file
from backend.app.services.llm_orchestrator import LLMProviderError
from backend.app.services.profile_ingestion import parse_resume_text, persist_profile_ingestion

router = APIRouter(prefix="/profile", tags=["profile"])

DBSession = Annotated[Session, Depends(get_db)]


def get_or_create_user_profile(session: Session, user: User) -> MasterProfile:
    assert user.id is not None
    profile = session.exec(select(MasterProfile).where(MasterProfile.user_id == user.id)).first()
    if not profile:
        profile = MasterProfile(
            user_id=user.id,
            full_name="",
            headline=None,
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)
    return profile


def get_or_create_default_profile(session: Session) -> MasterProfile:
    user = ensure_default_user(session)
    return get_or_create_user_profile(session, user)


def serialize_experience(exp: Experience) -> ExperienceRead:
    bullets = [BulletItem.model_validate(b) for b in (exp.bullets_json or [])]
    return ExperienceRead(
        id=exp.id,  # type: ignore[arg-type]
        profile_id=exp.profile_id,
        company=exp.company,
        role=exp.role,
        location=exp.location,
        start_date=exp.start_date,
        end_date=exp.end_date,
        is_current=exp.is_current,
        is_academic=exp.is_academic,
        bullets_json=bullets,
    )


def serialize_profile(profile: MasterProfile, session: Session) -> ProfileRead:
    experiences = session.exec(
        select(Experience).where(Experience.profile_id == profile.id).order_by(col(Experience.id))
    ).all()

    return ProfileRead(
        id=profile.id,  # type: ignore[arg-type]
        user_id=profile.user_id,
        full_name=profile.full_name,
        headline=profile.headline,
        contact_json=profile.contact_json,
        summary_base=profile.summary_base,
        skills_json=profile.skills_json,
        languages_json=profile.languages_json,
        experiences=[serialize_experience(exp) for exp in experiences],
    )


@router.get("", response_model=ProfileRead)
def get_profile(session: DBSession, current_user: CurrentUser) -> ProfileRead:
    profile = get_or_create_user_profile(session, current_user)
    return serialize_profile(profile, session)


@router.put("", response_model=ProfileRead)
def update_profile(
    data: ProfileUpdate, session: DBSession, current_user: CurrentUser
) -> ProfileRead:
    profile = get_or_create_user_profile(session, current_user)

    if data.full_name is not None:
        profile.full_name = data.full_name
    if data.headline is not None:
        profile.headline = data.headline
    if data.contact_json is not None:
        profile.contact_json = data.contact_json
    if data.summary_base is not None:
        profile.summary_base = data.summary_base
    if data.skills_json is not None:
        profile.skills_json = data.skills_json
    if data.languages_json is not None:
        profile.languages_json = data.languages_json

    session.add(profile)
    session.commit()
    session.refresh(profile)
    return serialize_profile(profile, session)


@router.post("/experiences", response_model=ExperienceRead, status_code=status.HTTP_201_CREATED)
def create_experience(
    data: ExperienceCreate, session: DBSession, current_user: CurrentUser
) -> ExperienceRead:
    profile = get_or_create_user_profile(session, current_user)
    assert current_user.id is not None
    exp = Experience(
        user_id=current_user.id,
        profile_id=profile.id,  # type: ignore[arg-type]
        company=data.company,
        role=data.role,
        location=data.location,
        start_date=data.start_date,
        end_date=data.end_date,
        is_current=data.is_current,
        is_academic=data.is_academic,
        bullets_json=[b.model_dump() for b in data.bullets_json],
    )
    session.add(exp)
    session.commit()
    session.refresh(exp)
    return serialize_experience(exp)


@router.put("/experiences/{experience_id}", response_model=ExperienceRead)
def update_experience(
    experience_id: int,
    data: ExperienceUpdate,
    session: DBSession,
    current_user: CurrentUser,
) -> ExperienceRead:
    exp = session.get(Experience, experience_id)
    if not exp or exp.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Experience not found")

    if data.company is not None:
        exp.company = data.company
    if data.role is not None:
        exp.role = data.role
    if data.location is not None:
        exp.location = data.location
    if data.start_date is not None:
        exp.start_date = data.start_date
    if data.end_date is not None:
        exp.end_date = data.end_date
    if data.is_current is not None:
        exp.is_current = data.is_current
    if data.is_academic is not None:
        exp.is_academic = data.is_academic
    if data.bullets_json is not None:
        exp.bullets_json = [b.model_dump() for b in data.bullets_json]

    session.add(exp)
    session.commit()
    session.refresh(exp)
    return serialize_experience(exp)


@router.delete("/experiences/{experience_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_experience(experience_id: int, session: DBSession, current_user: CurrentUser) -> None:
    exp = session.get(Experience, experience_id)
    if not exp or exp.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Experience not found")

    session.delete(exp)
    session.commit()
    return None


@router.post("/upload", response_model=ProfileRead)
async def upload_resume(
    session: DBSession,
    file: Annotated[UploadFile, File()],
    current_user: CurrentUser,
) -> ProfileRead:
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="File name is required."
        )

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="File size exceeds maximum limit of 10MB.",
        )

    try:
        extracted_text = extract_text_from_file(file.filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        parsed_data = parse_resume_text(extracted_text, session)
    except LLMProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM ingestion failed: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process resume text: {exc}",
        ) from exc

    profile = get_or_create_user_profile(session, current_user)
    try:
        persist_profile_ingestion(session, profile, parsed_data)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database persistence failed: {exc}",
        ) from exc

    return serialize_profile(profile, session)
