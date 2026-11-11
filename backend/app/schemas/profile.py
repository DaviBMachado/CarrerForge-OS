from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BulletItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text: str = Field(
        ...,
        min_length=1,
        description="Atomic description of achievement or responsibility",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Competency and skill tags for filtering",
    )


class ExperienceBase(BaseModel):
    company: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    is_academic: bool = False
    is_contract: bool = False
    bullets_json: list[BulletItem] = Field(default_factory=list)


class ExperienceCreate(ExperienceBase):
    pass


class ExperienceUpdate(BaseModel):
    company: str | None = None
    role: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool | None = None
    is_academic: bool | None = None
    is_contract: bool | None = None
    bullets_json: list[BulletItem] | None = None


class ExperienceRead(ExperienceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: int


class ProfileUpdate(BaseModel):
    full_name: str | None = None
    headline: str | None = None
    contact_json: dict[str, Any] | list[Any] | None = None
    summary_base: str | None = None
    skills_json: dict[str, Any] | list[Any] | None = None
    languages_json: list[Any] | dict[str, Any] | None = None


class ProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    full_name: str
    headline: str | None = None
    contact_json: Any | None = None
    summary_base: str | None = None
    skills_json: Any | None = None
    languages_json: Any | None = None
    experiences: list[ExperienceRead] = Field(default_factory=list)
