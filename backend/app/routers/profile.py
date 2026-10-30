from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from backend.app.db import get_db
from backend.app.models import Experience, MasterProfile, User
from backend.app.schemas.profile import (
    ExperienceCreate,
    ExperienceRead,
    ExperienceUpdate,
    LegacyImportResponse,
    ProfileRead,
    ProfileUpdate,
)
from backend.app.services.legacy_importer import import_legacy_data

router = APIRouter(prefix="/profile", tags=["profile"])

DBSession = Annotated[Session, Depends(get_db)]


def get_or_create_default_profile(session: Session) -> MasterProfile:
    user = session.exec(select(User).where(User.email == "default@careerforge.local")).first()
    if not user:
        user = User(email="default@careerforge.local")
        session.add(user)
        session.commit()
        session.refresh(user)

    profile = session.exec(
        select(MasterProfile).where(MasterProfile.user_id == user.id)
    ).first()
    if not profile:
        profile = MasterProfile(
            user_id=user.id,
            full_name="Davi Bernardes Machado",
            headline="Engenheiro de Software Backend",
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)
    return profile


def serialize_experience(exp: Experience) -> ExperienceRead:
    return ExperienceRead(
        id=exp.id,  # type: ignore[arg-type]
        profile_id=exp.profile_id,
        company=exp.company,
        role=exp.role,
        location=exp.location,
        start_date=exp.start_date,
        end_date=exp.end_date,
        is_current=exp.is_current,
        bullets_json=exp.bullets_json or [],
    )


def serialize_profile(profile: MasterProfile, session: Session) -> ProfileRead:
    experiences = session.exec(
        select(Experience)
        .where(Experience.profile_id == profile.id)
        .order_by(Experience.id)
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
def get_profile(session: DBSession) -> ProfileRead:
    profile = get_or_create_default_profile(session)
    return serialize_profile(profile, session)


@router.put("", response_model=ProfileRead)
def update_profile(data: ProfileUpdate, session: DBSession) -> ProfileRead:
    profile = get_or_create_default_profile(session)

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
    data: ExperienceCreate, session: DBSession
) -> ExperienceRead:
    profile = get_or_create_default_profile(session)
    exp = Experience(
        profile_id=profile.id,  # type: ignore[arg-type]
        company=data.company,
        role=data.role,
        location=data.location,
        start_date=data.start_date,
        end_date=data.end_date,
        is_current=data.is_current,
        bullets_json=[b.model_dump() for b in data.bullets_json],
    )
    session.add(exp)
    session.commit()
    session.refresh(exp)
    return serialize_experience(exp)


@router.put("/experiences/{experience_id}", response_model=ExperienceRead)
def update_experience(
    experience_id: int, data: ExperienceUpdate, session: DBSession
) -> ExperienceRead:
    profile = get_or_create_default_profile(session)
    exp = session.exec(
        select(Experience).where(
            Experience.id == experience_id, Experience.profile_id == profile.id
        )
    ).first()
    if not exp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Experience not found"
        )

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
    if data.bullets_json is not None:
        exp.bullets_json = [b.model_dump() for b in data.bullets_json]

    session.add(exp)
    session.commit()
    session.refresh(exp)
    return serialize_experience(exp)


@router.delete("/experiences/{experience_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_experience(experience_id: int, session: DBSession) -> None:
    profile = get_or_create_default_profile(session)
    exp = session.exec(
        select(Experience).where(
            Experience.id == experience_id, Experience.profile_id == profile.id
        )
    ).first()
    if not exp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Experience not found"
        )

    session.delete(exp)
    session.commit()
    return None


@router.post("/import-legacy", response_model=LegacyImportResponse)
def import_legacy_profile(session: DBSession) -> LegacyImportResponse:
    profile = get_or_create_default_profile(session)
    result = import_legacy_data(session, profile)
    return LegacyImportResponse(**result)
