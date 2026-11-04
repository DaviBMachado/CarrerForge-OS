import re
from datetime import UTC, datetime
from typing import Any
from sqlalchemy.orm import validates
from sqlmodel import JSON, Field, Relationship, SQLModel

_EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+$")


class User(SQLModel, table=True):
    __tablename__: Any = "users"

    id: int | None = Field(default=None, primary_key=True)
    email: str | None = Field(default=None, index=True, unique=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @validates("email")
    def validate_email_address(self, key: str, value: Any) -> str | None:
        if value is None or value == "":
            return None
        clean = str(value).strip()
        if not clean:
            return None
        if not _EMAIL_REGEX.match(clean):
            raise ValueError(f"Invalid email address: {value}")
        return clean

    master_profiles: list["MasterProfile"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    job_postings: list["JobPosting"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class MasterProfile(SQLModel, table=True):
    __tablename__: Any = "master_profiles"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    full_name: str
    headline: str | None = None
    contact_json: Any | None = Field(default=None, sa_type=JSON)
    summary_base: str | None = None
    skills_json: Any | None = Field(default=None, sa_type=JSON)
    languages_json: Any | None = Field(default=None, sa_type=JSON)

    user: User | None = Relationship(back_populates="master_profiles")
    experiences: list["Experience"] = Relationship(
        back_populates="profile",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Experience(SQLModel, table=True):
    __tablename__: Any = "experiences"

    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="master_profiles.id", ondelete="CASCADE", index=True)
    company: str = Field(index=True)
    role: str
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = Field(default=False)
    bullets_json: list[dict[str, Any]] | None = Field(default=None, sa_type=JSON)

    profile: MasterProfile | None = Relationship(back_populates="experiences")


class JobPosting(SQLModel, table=True):
    __tablename__: Any = "job_postings"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    platform: str = Field(default="generic", index=True)
    external_id: str | None = Field(default=None, index=True)
    title: str = Field(index=True)
    company: str = Field(index=True)
    location: str | None = None
    remote_type: str | None = None
    url: str | None = None
    raw_description: str
    cleaned_text: str | None = None
    language: str = Field(default="en")
    status: str = Field(default="discovered", index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    user: User | None = Relationship(back_populates="job_postings")
    artifacts: list["TailoredArtifact"] = Relationship(
        back_populates="job_posting",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class TailoredArtifact(SQLModel, table=True):
    __tablename__: Any = "tailored_artifacts"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job_postings.id", ondelete="CASCADE", index=True)
    selected_persona: str | None = None
    tailored_summary: str | None = None
    tailored_experience_json: Any | None = Field(default=None, sa_type=JSON)
    cover_letter_text: str | None = None
    gupy_rundown_json: Any | None = Field(default=None, sa_type=JSON)
    gupy_qa_json: Any | None = Field(default=None, sa_type=JSON)
    resume_pdf_path: str | None = None
    resume_docx_path: str | None = None

    job_posting: JobPosting | None = Relationship(back_populates="artifacts")


class SystemSetting(SQLModel, table=True):
    __tablename__: Any = "system_settings"

    key: str = Field(primary_key=True, index=True)
    value_encrypted_or_plain: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = [
    "User",
    "MasterProfile",
    "Experience",
    "JobPosting",
    "TailoredArtifact",
    "SystemSetting",
]
