from datetime import UTC, datetime

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from backend.app.db import get_db
from backend.app.models import (
    Experience,
    JobPosting,
    MasterProfile,
    SystemSetting,
    TailoredArtifact,
    User,
)


@pytest.fixture
def session():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(Engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as sess:
        yield sess


def test_table_creation(session: Session):
    assert session.bind is not None
    inspector = inspect(session.bind)
    tables = inspector.get_table_names()
    expected_tables = {
        "users",
        "master_profiles",
        "experiences",
        "job_postings",
        "tailored_artifacts",
        "system_settings",
    }
    assert expected_tables.issubset(set(tables))


def test_user_email_validation(session: Session):
    # Optional / None email
    user_none = User(email=None)
    session.add(user_none)
    session.commit()
    assert user_none.id is not None
    assert user_none.email is None

    # Valid local domain email
    user_local = User(email="default@careerforge.local")
    session.add(user_local)
    session.commit()
    assert user_local.email == "default@careerforge.local"

    # Invalid email formats must raise ValueError
    import pytest

    with pytest.raises(ValueError, match="Invalid email address"):
        User(email="not-an-email")

    with pytest.raises(ValueError, match="Invalid email address"):
        User(email="@domain.com")

    with pytest.raises(ValueError, match="Invalid email address"):
        User(email="user@domain")


def test_user_and_profile_cascade(session: Session):
    user = User(email="test_user@example.com")
    session.add(user)
    session.commit()
    session.refresh(user)

    assert user.id is not None
    profile = MasterProfile(
        user_id=user.id,
        full_name="Jane Doe",
        headline="Senior AI Engineer",
        contact_json={"phone": "+123456789", "linkedin": "linkedin.com/in/janedoe"},
        skills_json=["Python", "FastAPI", "SQLModel"],
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)

    assert profile.id is not None
    assert profile.skills_json == ["Python", "FastAPI", "SQLModel"]

    # Test cascade delete: deleting user should delete profile
    session.delete(user)
    session.commit()

    deleted_profile = session.exec(
        select(MasterProfile).where(MasterProfile.id == profile.id)
    ).first()
    assert deleted_profile is None


def test_profile_and_experience_cascade(session: Session):
    user = User(email="experience_test@example.com")
    session.add(user)
    session.commit()

    assert user.id is not None
    profile = MasterProfile(user_id=user.id, full_name="John Dev")
    session.add(profile)
    session.commit()

    assert profile.id is not None
    exp = Experience(
        profile_id=profile.id,
        company="Tech Corp",
        role="Backend Engineer",
        bullets_json=[{"text": "Engineered data pipeline", "tags": ["python", "etl"]}],
    )
    session.add(exp)
    session.commit()
    session.refresh(exp)

    assert exp.id is not None
    assert exp.bullets_json is not None
    assert exp.bullets_json[0]["tags"] == ["python", "etl"]

    # Deleting profile deletes experience
    session.delete(profile)
    session.commit()

    deleted_exp = session.exec(select(Experience).where(Experience.id == exp.id)).first()
    assert deleted_exp is None


def test_job_posting_and_tailored_artifact_cascade(session: Session):
    user = User(email="artifact_test@example.com")
    session.add(user)
    session.commit()

    assert user.id is not None
    job = JobPosting(
        user_id=user.id,
        title="Software Architect",
        company="Global Tech",
        raw_description="Build distributed systems",
    )
    session.add(job)
    session.commit()

    assert job.id is not None
    artifact = TailoredArtifact(
        job_id=job.id,
        selected_persona="Distributed Systems Specialist",
        tailored_summary="Experienced builder of scalable systems",
        tailored_experience_json=[
            {"role": "Lead Architect", "bullets": ["Designed event pipeline"]}
        ],
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    assert artifact.id is not None
    assert artifact.tailored_experience_json is not None
    assert artifact.tailored_experience_json[0]["role"] == "Lead Architect"

    session.delete(job)
    session.commit()

    deleted_artifact = session.exec(
        select(TailoredArtifact).where(TailoredArtifact.id == artifact.id)
    ).first()
    assert deleted_artifact is None


def test_system_settings(session: Session):
    setting = SystemSetting(
        key="OPENAI_API_KEY",
        value_encrypted_or_plain="sk-test-secret-value",
        updated_at=datetime.now(UTC),
    )
    session.add(setting)
    session.commit()

    retrieved = session.exec(
        select(SystemSetting).where(SystemSetting.key == "OPENAI_API_KEY")
    ).first()
    assert retrieved is not None
    assert retrieved.value_encrypted_or_plain == "sk-test-secret-value"


def test_get_db_generator():
    db_gen = get_db()
    session = next(db_gen)
    assert isinstance(session, Session)
    try:
        next(db_gen)
    except StopIteration:
        pass
