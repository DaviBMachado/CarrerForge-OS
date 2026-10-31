import json

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from backend.app.db import get_db
from backend.app.models import Experience, JobPosting, MasterProfile, TailoredArtifact, User
from backend.app.routers.tailor import get_llm_orchestrator
from backend.app.schemas.settings import SettingsUpdate
from backend.app.services.llm_orchestrator import LLMOrchestrator
from backend.app.services.resume_compiler import ResumeBlock, ResumeSection, ResumeSpec
from backend.app.services.settings import update_settings
from backend.app.services.tailoring_engine import (
    FactualGuardrailError,
    build_tailoring_system_prompt,
    extract_ats_keywords,
    solve_screening_question,
    tailor_job_application,
    verify_factual_guardrails,
)
from backend.main import app


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess


@pytest.fixture
def candidate_setup(session: Session) -> tuple[User, MasterProfile, JobPosting]:
    user = User(email="davi@careerforge.local")
    session.add(user)
    session.flush()
    assert user.id is not None

    profile = MasterProfile(
        user_id=user.id,
        full_name="Davi Bernardes Machado",
        headline="Backend Software Engineer",
        contact_json={"email": "davi@example.com", "location": "São Paulo, SP"},
        summary_base="Backend engineer experienced with Python, AWS, FastAPI, and REST APIs.",
        skills_json={
            "Backend": ["Python", "FastAPI", "Django", "REST", "PostgreSQL"],
            "Cloud": ["AWS", "Lambda", "Step Functions", "S3", "Docker", "Terraform"],
            "AI": ["LLM", "RAG", "LangChain"],
        },
        languages_json=["Português (Nativo)", "Inglês (Avançado)"],
    )
    session.add(profile)
    session.flush()
    assert profile.id is not None

    exp = Experience(
        profile_id=profile.id,
        company="Itaú Unibanco",
        role="Software Engineering Intern",
        location="São Paulo, SP",
        start_date="02/2024",
        end_date="06/2025",
        bullets_json=[
            {
                "text": (
                    "Built Python REST integrations syncing 10,000+ records/day "
                    "between ServiceNow and AWS S3."
                ),
                "tags": ["Python", "REST", "AWS", "S3", "ServiceNow"],
            },
            {
                "text": "Automated 10+ manual workflows using AWS Step Functions and Lambda.",
                "tags": ["AWS", "Step Functions", "Lambda"],
            },
        ],
    )
    session.add(exp)

    job = JobPosting(
        user_id=user.id,
        platform="linkedin",
        title="Senior Backend Python Engineer",
        company="Acme Fintech",
        raw_description=(
            "We are looking for a Backend Python Engineer to build scalable microservices "
            "using Python, FastAPI, PostgreSQL, AWS Lambda, Docker, and Kubernetes."
        ),
    )
    session.add(job)
    session.commit()
    session.refresh(profile)
    session.refresh(job)
    return user, profile, job


def test_extract_ats_keywords_filters_to_grounded_profile_skills(
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    _, profile, job = candidate_setup
    job_kws, grounded_kws, match_pct = extract_ats_keywords(job.raw_description, profile)

    assert "python" in job_kws
    assert "fastapi" in job_kws
    assert "kubernetes" in job_kws  # Present in job, NOT in profile

    assert "python" in grounded_kws
    assert "fastapi" in grounded_kws
    assert "postgresql" in grounded_kws
    assert "aws" in grounded_kws
    assert "lambda" in grounded_kws
    assert "docker" in grounded_kws
    assert "kubernetes" not in grounded_kws
    assert 0 < match_pct < 100


def test_guardrails_reject_hallucinated_technologies_and_companies(
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    _, profile, _ = candidate_setup

    # 1. Hallucinated technology (Kubernetes / Golang not in profile)
    hallucinated_tech_spec = ResumeSpec(
        name="Davi Bernardes Machado",
        title="Backend Python Engineer",
        sections=[
            ResumeSection(
                title="Experience",
                blocks=[
                    ResumeBlock(
                        type="subheading",
                        text="Software Engineering Intern | Itaú Unibanco",
                    ),
                    ResumeBlock(
                        type="bullet",
                        text=(
                            "Deployed Golang microservices on Kubernetes clusters with AWS Lambda."
                        ),
                    ),
                ],
            )
        ],
    )
    with pytest.raises(FactualGuardrailError, match="kubernetes"):
        verify_factual_guardrails(hallucinated_tech_spec, profile)

    # 2. Hallucinated company (Nubank not in profile experiences)
    hallucinated_company_spec = ResumeSpec(
        name="Davi Bernardes Machado",
        title="Backend Python Engineer",
        sections=[
            ResumeSection(
                title="Experience",
                blocks=[
                    ResumeBlock(
                        type="subheading",
                        text="Senior Software Engineer | Nubank (2023 - 2024)",
                    ),
                    ResumeBlock(
                        type="bullet",
                        text="Built Python REST APIs on AWS Lambda.",
                    ),
                ],
            )
        ],
    )
    with pytest.raises(FactualGuardrailError, match="Nubank"):
        verify_factual_guardrails(hallucinated_company_spec, profile)


def test_tailor_job_application_generates_spec_and_persists_artifact(
    session: Session,
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    _, profile, job = candidate_setup
    update_settings(
        session,
        SettingsUpdate(active_provider="gemini", gemini_api_key="AIzaMockTailorKey123"),
    )

    llm_response_payload = {
        "resume_spec": {
            "name": "Davi Bernardes Machado",
            "title": "Davi Bernardes Machado - Backend Python Engineer",
            "contact": "São Paulo, SP | davi@example.com",
            "filename_title": "Resume_Davi_Bernardes_Machado_Acme_Fintech",
            "sections": [
                {
                    "title": "Professional Summary",
                    "blocks": [
                        {
                            "type": "paragraph",
                            "text": (
                                "Backend Python Engineer experienced with FastAPI, PostgreSQL, "
                                "Docker, and serverless architectures on AWS Lambda."
                            ),
                        }
                    ],
                },
                {
                    "title": "Technical Skills",
                    "blocks": [
                        {
                            "type": "skillline",
                            "text": (
                                "**Backend & Cloud:** Python, FastAPI, PostgreSQL, "
                                "AWS Lambda, S3, Docker"
                            ),
                        }
                    ],
                },
                {
                    "title": "Professional Experience",
                    "blocks": [
                        {
                            "type": "subheading",
                            "text": (
                                "Software Engineering Intern | Itaú Unibanco (02/2024 - 06/2025)"
                            ),
                        },
                        {
                            "type": "bullet",
                            "text": (
                                "Developed Python REST APIs and data integrations syncing "
                                "10,000+ records/day between ServiceNow and AWS S3."
                            ),
                        },
                        {
                            "type": "bullet",
                            "text": (
                                "Automated 10+ manual workflows using AWS Step Functions "
                                "and AWS Lambda."
                            ),
                        },
                    ],
                },
            ],
        },
        "cover_letter": (
            "Dear Hiring Team at Acme Fintech, I am writing to apply for the "
            "Senior Backend Python Engineer role. My background in Python, FastAPI, "
            "and AWS aligns directly with your platform goals.\n\n"
            "At Itaú Unibanco, I engineered Python REST integrations processing "
            "10,000+ records per day into AWS S3 and automated 10+ critical workflows "
            "with AWS Step Functions and Lambda.\n\n"
            "I welcome the opportunity to discuss how my backend engineering "
            "experience can support Acme Fintech. Thank you for your time and consideration."
        ),
    }

    captured_Prompts: list[dict] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_Prompts.append(body)
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": json.dumps(llm_response_payload)}]}}]
            },
        )

    orchestrator = LLMOrchestrator(
        session,
        http_client=httpx.Client(transport=httpx.MockTransport(mock_handler)),
    )

    result = tailor_job_application(session, job, profile, orchestrator=orchestrator)

    assert result.job_id == job.id
    assert result.language == "en"
    assert result.selected_persona == "Backend Python Engineer"
    assert isinstance(result.resume_spec, ResumeSpec)
    assert len(result.cover_letter_text.split("\n\n")) == 3

    # Verify system prompt enforced factual guardrails
    sys_instruction = captured_Prompts[0]["systemInstruction"]["parts"][0]["text"]
    assert "NEVER fabricate" in sys_instruction
    assert "kubernetes" not in result.ats_keywords

    # Verify persistence in TailoredArtifact table
    saved = session.get(TailoredArtifact, result.artifact_id)
    assert saved is not None
    assert saved.job_id == job.id
    assert saved.selected_persona == "Backend Python Engineer"
    assert saved.cover_letter_text == result.cover_letter_text
    assert isinstance(saved.tailored_experience_json, dict)
    assert saved.tailored_experience_json["name"] == "Davi Bernardes Machado"
    assert "FastAPI" in (saved.tailored_summary or "")


def test_build_tailoring_system_prompt_includes_strict_rules():
    prompt = build_tailoring_system_prompt(
        language="pt-br",
        persona="AI Automation Engineer",
        grounded_keywords=["python", "llm", "rag"],
    )
    assert "Portuguese (pt-BR)" in prompt
    assert "AI Automation Engineer" in prompt
    assert "python, llm, rag" in prompt
    assert "3-paragraph cover letter" in prompt


def test_gupy_rundown_generated_and_persisted(
    session: Session,
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    from fastapi.testclient import TestClient

    user, profile, job = candidate_setup
    user.email = "default@careerforge.local"
    session.add(user)
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            resp = client.get(f"/api/tailor/{job.id}/gupy-rundown")
            assert resp.status_code == 200
            data = resp.json()
            assert "Davi Bernardes Machado" in data["short_bio"]
            assert any("10,000+" in m for m in data["impact_metrics"])
            assert any("10+" in m for m in data["impact_metrics"])
            assert "Python" in data["prioritized_skill_tags"]
            assert "FastAPI" in data["prioritized_skill_tags"]
            assert "kubernetes" not in [t.lower() for t in data["prioritized_skill_tags"]]
            assert data["selected_persona"] == "Backend Python Engineer"

            artifact = session.exec(
                select(TailoredArtifact).where(TailoredArtifact.job_id == job.id)
            ).first()
            assert artifact is not None
            assert isinstance(artifact.gupy_rundown_json, dict)
            assert artifact.gupy_rundown_json["short_bio"] == data["short_bio"]
    finally:
        app.dependency_overrides.clear()


def test_solve_question_endpoint_returns_grounded_answer_and_blocks_hallucination(
    session: Session,
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    from fastapi.testclient import TestClient

    user, _, job = candidate_setup
    user.email = "default@careerforge.local"
    session.add(user)
    session.commit()

    update_settings(
        session,
        SettingsUpdate(active_provider="gemini", gemini_api_key="AIzaMockQAKey123"),
    )

    mock_answer_payload = {
        "answer": (
            "At Itaú Unibanco, I handled complex integration requirements by building "
            "Python REST services syncing 10,000+ records/day into AWS S3 and automating "
            "10+ manual workflows with AWS Step Functions and Lambda."
        ),
        "grounded_experiences": ["Itaú Unibanco"],
    }

    def mock_qa_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": json.dumps(mock_answer_payload)}]}}]
            },
        )

    def override_get_db():
        yield session

    def override_get_llm():
        return LLMOrchestrator(
            session,
            http_client=httpx.Client(transport=httpx.MockTransport(mock_qa_handler)),
        )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_llm_orchestrator] = override_get_llm
    try:
        with TestClient(app) as client:
            resp = client.post(
                f"/api/tailor/{job.id}/solve-question",
                json={"question": "How do you handle ambiguous requirements and scale workflows?"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "10,000+" in data["answer"]
            assert "Itaú Unibanco" in data["grounded_experiences"]
            assert data["language"] == "en"

            artifact = session.exec(
                select(TailoredArtifact).where(TailoredArtifact.job_id == job.id)
            ).first()
            assert artifact is not None
            assert isinstance(artifact.gupy_qa_json, list)
            assert len(artifact.gupy_qa_json) == 1
            assert artifact.gupy_qa_json[0]["answer"] == data["answer"]
    finally:
        app.dependency_overrides.clear()


def test_solve_question_rejects_ungrounded_tech_hallucination(
    session: Session,
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    _, profile, job = candidate_setup
    update_settings(
        session,
        SettingsUpdate(active_provider="gemini", gemini_api_key="AIzaMockQAKey123"),
    )

    bad_payload = {
        "answer": "I deployed Kubernetes clusters using Golang and Rust in production.",
        "grounded_experiences": ["Itaú Unibanco"],
    }

    orchestrator = LLMOrchestrator(
        session,
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(
                    200,
                    json={
                        "candidates": [{"content": {"parts": [{"text": json.dumps(bad_payload)}]}}]
                    },
                )
            )
        ),
    )

    with pytest.raises(FactualGuardrailError, match="golang"):
        solve_screening_question(
            session,
            job,
            profile,
            question="Tell us about your infrastructure experience.",
            orchestrator=orchestrator,
        )
