import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, col, create_engine, select

from backend.app.db import get_db
from backend.app.models import Experience
from backend.app.routers.profile import get_or_create_default_profile
from backend.app.services.llm_orchestrator import LLMProviderError
from backend.app.services.profile_ingestion import (
    normalize_parsed_profile,
    parse_resume_text,
    persist_profile_ingestion,
)
from backend.main import app

MOCK_PARSED_OUTPUT = {
    "full_name": "Ana Silva",
    "headline": "Arquiteta Cloud & DevOps",
    "summary_base": "Especialista em infraestrutura resiliente e Kubernetes.",
    "contact": {
        "email": "ana.silva@example.com",
        "phone": "+55 21 98888-7777",
        "location": "Rio de Janeiro, Brasil",
        "linkedin": "https://linkedin.com/in/anasilva",
        "github": "https://github.com/anasilva",
    },
    "skills": {
        "Cloud/DevOps": ["Kubernetes", "Terraform", "AWS"],
        "Languages": ["Go", "Python"],
    },
    "languages": ["Português (Nativo)", "Inglês (Fluente)"],
    "experiences": [
        {
            "company": "Tech Corp",
            "role": "Senior Cloud Engineer",
            "location": "Remoto",
            "start_date": "2021-02",
            "end_date": None,
            "is_current": True,
            "bullets": [
                {
                    "text": "Redução de 40% nos custos de nuvem com migração para spot instances.",
                    "tags": ["aws", "terraform"],
                }
            ],
        }
    ],
}


@pytest.fixture
def db_session():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(Engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(test_engine)

    with Session(test_engine) as sess:
        yield sess


@pytest.fixture
def client(db_session: Session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_normalize_parsed_profile():
    raw = {
        "full_name": "  Carlos Santos  ",
        "headline": " Backend Developer ",
        "contact": {"email": "carlos@test.com"},
        "skills": ["Python", "Docker"],
        "languages": "Inglês, Espanhol",
        "experiences": [
            {
                "company": "Startup X",
                "role": "Dev",
                "bullets": ["Desenvolveu APIs"],
            }
        ],
    }
    normalized = normalize_parsed_profile(raw)
    assert normalized["full_name"] == "Carlos Santos"
    assert normalized["headline"] == "Backend Developer"
    assert normalized["contact"] == {"email": "carlos@test.com"}
    assert normalized["skills"] == {"General": ["Python", "Docker"]}
    assert normalized["languages"] == ["Inglês", "Espanhol"]
    assert len(normalized["experiences"]) == 1
    assert normalized["experiences"][0]["bullets"][0]["text"] == "Desenvolveu APIs"


def test_parse_resume_text_with_mock_orchestrator(db_session: Session):
    mock_orchestrator = MagicMock()
    mock_orchestrator.generate_json.return_value = MOCK_PARSED_OUTPUT

    result = parse_resume_text(
        "Curriculo de Ana Silva...", db_session, orchestrator=mock_orchestrator
    )
    assert result["full_name"] == "Ana Silva"
    assert result["headline"] == "Arquiteta Cloud & DevOps"
    assert len(result["experiences"]) == 1


def test_persist_profile_ingestion_atomic(db_session: Session):
    profile = get_or_create_default_profile(db_session)
    assert profile.id is not None
    # Add initial dummy experience
    initial_exp = Experience(
        profile_id=profile.id,
        company="Old Company",
        role="Junior",
    )
    db_session.add(initial_exp)
    db_session.commit()

    persist_profile_ingestion(db_session, profile, MOCK_PARSED_OUTPUT)

    db_session.refresh(profile)
    assert profile.full_name == "Ana Silva"
    assert profile.headline == "Arquiteta Cloud & DevOps"

    exps = db_session.exec(select(Experience).where(Experience.profile_id == profile.id)).all()
    assert len(exps) == 1
    assert exps[0].company == "Tech Corp"
    assert exps[0].bullets_json is not None
    assert exps[0].bullets_json[0]["text"].startswith("Redução de 40%")


def test_upload_endpoint_success(client: TestClient):
    with patch("backend.app.routers.profile.parse_resume_text", return_value=MOCK_PARSED_OUTPUT):
        file_content = b"# Ana Silva\nArquiteta Cloud\nTech Corp - Senior"
        response = client.post(
            "/api/profile/upload",
            files={"file": ("curriculo.md", io.BytesIO(file_content), "text/markdown")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["full_name"] == "Ana Silva"
        assert data["headline"] == "Arquiteta Cloud & DevOps"
        assert len(data["experiences"]) == 1
        assert data["experiences"][0]["company"] == "Tech Corp"


def test_upload_endpoint_unsupported_format(client: TestClient):
    response = client.post(
        "/api/profile/upload",
        files={"file": ("resume.png", io.BytesIO(b"fake image data"), "image/png")},
    )
    assert response.status_code == 400
    assert "Unsupported file format" in response.json()["detail"]


def test_upload_endpoint_size_limit(client: TestClient):
    oversized = b"a" * (10 * 1024 * 1024 + 10)
    response = client.post(
        "/api/profile/upload",
        files={"file": ("resume.txt", io.BytesIO(oversized), "text/plain")},
    )
    assert response.status_code == 413


def test_upload_endpoint_llm_failure(client: TestClient):
    with patch(
        "backend.app.routers.profile.parse_resume_text",
        side_effect=LLMProviderError("Provider unavailable"),
    ):
        response = client.post(
            "/api/profile/upload",
            files={"file": ("resume.txt", io.BytesIO(b"Valid resume text"), "text/plain")},
        )
        assert response.status_code == 502
        assert "LLM ingestion failed" in response.json()["detail"]


def test_normalize_and_persist_academic_and_professional_separation(db_session: Session):
    profile = get_or_create_default_profile(db_session)
    assert profile.id is not None

    raw_data = {
        "full_name": "Lucas Aluno",
        "headline": "Researcher & Software Engineer",
        "professional_experiences": [
            {
                "company": "Enterprise Tech Corp",
                "role": "Software Engineer",
                "location": "São Paulo",
                "start_date": "2022-01",
                "end_date": "2024-05",
                "is_current": False,
                "bullets": [{"text": "Maintained high-throughput APIs", "tags": ["python"]}],
            }
        ],
        "academic_projects": [
            {
                "company": "Universidade de São Paulo (USP)",
                "role": "Pesquisador de Iniciação Científica",
                "location": "São Paulo",
                "start_date": "2020-02",
                "end_date": "2021-12",
                "is_current": False,
                "bullets": [
                    {
                        "text": "Desenvolveu algoritmos de otimização para redes complexas.",
                        "tags": ["python", "graph-theory"],
                    }
                ],
            }
        ],
    }

    normalized = normalize_parsed_profile(raw_data)
    assert len(normalized["experiences"]) == 2

    corp = next(e for e in normalized["experiences"] if e["company"] == "Enterprise Tech Corp")
    acad = next(
        e for e in normalized["experiences"] if e["company"] == "Universidade de São Paulo (USP)"
    )

    assert corp["is_academic"] is False
    assert acad["is_academic"] is True

    persist_profile_ingestion(db_session, profile, normalized)
    db_session.refresh(profile)

    exps = db_session.exec(
        select(Experience)
        .where(Experience.profile_id == profile.id)
        .order_by(col(Experience.is_academic))
    ).all()
    assert len(exps) == 2
    assert exps[0].company == "Enterprise Tech Corp"
    assert exps[0].is_academic is False
    assert exps[1].company == "Universidade de São Paulo (USP)"
    assert exps[1].is_academic is True
