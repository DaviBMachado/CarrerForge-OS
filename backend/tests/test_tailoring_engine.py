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
from backend.app.services.resume_compiler import (
    ResumeBlock,
    ResumeCompiler,
    ResumeSection,
    ResumeSpec,
)
from backend.app.services.settings import update_settings
from backend.app.services.tailoring_engine import (
    FactualGuardrailError,
    _normalize_cover_letter,
    _serialize_profile_for_prompt,
    build_ats_system_prompt,
    build_human_curation_system_prompt,
    build_tailoring_system_prompt,
    extract_ats_keywords,
    generate_gupy_rundown,
    get_tailored_artifact,
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
    assert saved.tailored_experience_json["ats"]["name"] == "Davi Bernardes Machado"
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
    assert "Action + Scope + Metric" in prompt
    assert "courteous professional greeting" in prompt
    assert "Prohibit corporate flattery" in prompt
    assert "Conceptual Anti-AI directive" in prompt
    assert "Cargo (Contrato Temporário) | Empresa (Datas)" in prompt
    assert "rapid ramp-up" in prompt
    assert "autonomous delivery of agreed project scope" in prompt


def test_build_ats_and_human_prompts_contract_formatting_en_and_ptbr():
    # ATS English
    ats_en = build_ats_system_prompt("en", "Backend Lead", ["python", "aws"])
    assert "Role (Contract) | Company (Dates)" in ats_en
    assert "rapid ramp-up" in ats_en
    assert "autonomous delivery of agreed project scope" in ats_en

    # ATS Portuguese
    ats_pt = build_ats_system_prompt("pt-br", "Engenheiro Backend", ["python", "aws"])
    assert "Cargo (Contrato Temporário) | Empresa (Datas)" in ats_pt
    assert "rapid ramp-up" in ats_pt
    assert "autonomous delivery of agreed project scope" in ats_pt

    # Human Curation English
    human_en = build_human_curation_system_prompt("en", "Backend Lead", ["python", "aws"])
    assert "Role (Contract) | Company (Dates)" in human_en
    assert "rapid ramp-up" in human_en
    assert "autonomous delivery of agreed project scope" in human_en

    # Human Curation Portuguese
    human_pt = build_human_curation_system_prompt("pt-br", "Engenheiro Backend", ["python", "aws"])
    assert "Cargo (Contrato Temporário) | Empresa (Datas)" in human_pt
    assert "rapid ramp-up" in human_pt
    assert "autonomous delivery of agreed project scope" in human_pt


def test_screening_question_system_prompt_includes_contract_positioning(
    session: Session,
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    _, profile, job = candidate_setup
    update_settings(
        session,
        SettingsUpdate(active_provider="gemini", gemini_api_key="AIzaMockQAKey123"),
    )

    captured_prompts: list[str] = []

    def mock_llm_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_prompts.append(body["systemInstruction"]["parts"][0]["text"])
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "answer": "At Itaú Unibanco, I delivered scoped automation.",
                                            "grounded_experiences": ["Itaú Unibanco"],
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )

    orchestrator = LLMOrchestrator(
        session,
        http_client=httpx.Client(transport=httpx.MockTransport(mock_llm_handler)),
    )

    solve_screening_question(
        session=session,
        job=job,
        profile=profile,
        question="How do you handle contract deliverables and consulting scope?",
        orchestrator=orchestrator,
    )

    assert len(captured_prompts) == 1
    sys_prompt = captured_prompts[0]
    assert "Positioning Temporary Contracts" in sys_prompt
    assert "scoped consulting engagements" in sys_prompt
    assert "preventing defensive phrasing" in sys_prompt


def test_contract_experience_app_testable_milestone(
    session: Session,
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    _, profile, job = candidate_setup
    assert profile.id is not None

    # Add a temporary contract experience
    contract_exp = Experience(
        profile_id=profile.id,
        company="Contractor Corp",
        role="Cloud Consultant",
        location="Remote",
        start_date="01/2023",
        end_date="08/2023",
        is_contract=True,
        bullets_json=[
            {
                "text": "Delivered AWS migration ahead of schedule for financial data pipelines.",
                "tags": ["AWS", "Python"],
            }
        ],
    )
    session.add(contract_exp)
    session.commit()
    session.refresh(profile)

    spec_with_contract = ResumeSpec(
        name=profile.full_name,
        title="Backend Python Engineer",
        contact="São Paulo, SP | davi@example.com",
        filename_title="Resume_Davi_Contract",
        sections=[
            ResumeSection(
                title="Professional Experience",
                blocks=[
                    ResumeBlock(
                        type="subheading",
                        text="Cloud Consultant (Contract) | Contractor Corp (01/2023 - 08/2023)",
                    ),
                    ResumeBlock(
                        type="bullet",
                        text="Delivered AWS migration ahead of schedule for financial data pipelines.",
                    ),
                ],
            )
        ],
    )

    # 1. Guardrail verification passes with "(Contract)" in LHS
    verify_factual_guardrails(spec_with_contract, profile)

    # 2. PDF compilation succeeds without layout errors
    compiler = ResumeCompiler()
    pdf_stream, page_count = compiler.compile_pdf(spec_with_contract)
    assert len(pdf_stream.getvalue()) > 0
    assert page_count >= 1

    # 3. Repeat for pt-BR "(Contrato Temporário)"
    spec_with_contract_pt = ResumeSpec(
        name=profile.full_name,
        title="Engenheiro Backend Python",
        contact="São Paulo, SP | davi@example.com",
        filename_title="Curriculo_Davi_Contrato",
        sections=[
            ResumeSection(
                title="Experiência Profissional",
                blocks=[
                    ResumeBlock(
                        type="subheading",
                        text="Consultor Cloud (Contrato Temporário) | Contractor Corp (01/2023 - 08/2023)",
                    ),
                    ResumeBlock(
                        type="bullet",
                        text="Delivered AWS migration ahead of schedule for financial data pipelines.",
                    ),
                ],
            )
        ],
    )
    verify_factual_guardrails(spec_with_contract_pt, profile)
    pdf_stream_pt, page_count_pt = compiler.compile_pdf(spec_with_contract_pt)
    assert len(pdf_stream_pt.getvalue()) > 0
    assert page_count_pt >= 1


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

    captured_qa_requests: list[dict] = []

    def mock_qa_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_qa_requests.append(body)
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

            qa_sys_prompt = captured_qa_requests[0]["systemInstruction"]["parts"][0]["text"]
            assert "objective, impersonal, and binary" in qa_sys_prompt
            assert "ANTI-AI DIRECTIVES" in qa_sys_prompt

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


def test_get_tailored_artifact_retrieves_saved_spec_without_llm(
    session: Session,
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    _, profile, job = candidate_setup
    assert get_tailored_artifact(session, job, profile) is None

    # Persist mock artifact
    spec_data = {
        "name": profile.full_name,
        "title": "Backend Python Engineer",
        "contact": "davi@example.com",
        "filename_title": "Resume_Davi",
        "sections": [
            {
                "title": "Professional Experience",
                "blocks": [{"type": "bullet", "text": "Built Python APIs"}],
            }
        ],
    }
    assert job.id is not None
    assert job.id is not None
    artifact = TailoredArtifact(
        job_id=job.id,
        selected_persona="Backend Python Engineer",
        tailored_summary="Experienced backend engineer.",
        tailored_experience_json=spec_data,
        cover_letter_text="P1\n\nP2\n\nP3",
    )
    session.add(artifact)
    session.commit()

    retrieved = get_tailored_artifact(session, job, profile)
    assert retrieved is not None
    assert retrieved.job_id == job.id
    assert retrieved.selected_persona == "Backend Python Engineer"
    assert retrieved.cover_letter_text == "P1\n\nP2\n\nP3"
    assert retrieved.resume_spec.name == profile.full_name


def test_get_tailor_endpoint_and_spa_route_error(
    session: Session,
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    from fastapi.testclient import TestClient

    user, profile, job = candidate_setup

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            headers = {"X-User-Id": str(user.id)}

            # 1. 404 when artifact not yet generated
            resp_404 = client.get(f"/api/tailor/{job.id}", headers=headers)
            assert resp_404.status_code == 404
            assert "Tailored artifact not yet generated" in resp_404.json()["detail"]

            # 2. Persist artifact and verify 200 GET
            assert job.id is not None
            artifact = TailoredArtifact(
                job_id=job.id,
                selected_persona="Backend Python Engineer",
                tailored_summary="Experienced backend engineer.",
                tailored_experience_json={
                    "name": profile.full_name,
                    "title": "Backend Engineer",
                    "contact": "davi@example.com",
                    "filename_title": "Resume",
                    "sections": [],
                },
                cover_letter_text="Cover letter content",
            )
            session.add(artifact)
            session.commit()

            resp_200 = client.get(f"/api/tailor/{job.id}", headers=headers)
            assert resp_200.status_code == 200
            data = resp_200.json()
            assert data["job_id"] == job.id
            assert data["selected_persona"] == "Backend Python Engineer"
            assert data["cover_letter_text"] == "Cover letter content"

            # 3. Descriptive 404 for nonexistent API route
            resp_not_found = client.get("/api/nonexistent-endpoint")
            assert resp_not_found.status_code == 404
            assert (
                "API route '/api/nonexistent-endpoint' not found" in resp_not_found.json()["detail"]
            )
    finally:
        app.dependency_overrides.clear()


def test_solve_screening_question_persistent_cache_hit(
    session: Session,
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    _, profile, job = candidate_setup
    update_settings(
        session,
        SettingsUpdate(active_provider="gemini", gemini_api_key="AIzaMockQAKey123"),
    )

    llm_call_count = 0

    def mock_transport_handler(req: httpx.Request) -> httpx.Response:
        nonlocal llm_call_count
        llm_call_count += 1
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "answer": (
                                                "Handled high load by scaling FastAPI workflows "
                                                "with 10,000+ RPS at Itaú Unibanco."
                                            ),
                                            "grounded_experiences": ["Itaú Unibanco"],
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )

    orchestrator = LLMOrchestrator(
        session,
        http_client=httpx.Client(transport=httpx.MockTransport(mock_transport_handler)),
    )

    q = "How do you handle ambiguous requirements and scale workflows?"

    # 1. First call: executes LLM and caches
    res1 = solve_screening_question(session, job, profile, question=q, orchestrator=orchestrator)
    assert llm_call_count == 1
    assert "10,000+" in res1.answer

    # 2. Second call with varied casing and whitespace: hit cache without invoking LLM
    res2 = solve_screening_question(
        session,
        job,
        profile,
        question=f"  {q.upper()}  ",
        orchestrator=orchestrator,
    )
    assert llm_call_count == 1
    assert res2.answer == res1.answer
    assert res2.grounded_experiences == res1.grounded_experiences

    # 3. Verify rundown exposure
    rundown = generate_gupy_rundown(session, job, profile)
    assert len(rundown.screening_qa) == 1
    assert rundown.screening_qa[0].question == q
    assert rundown.screening_qa[0].answer == res1.answer


def test_human_curation_prompt_directives():
    prompt = build_human_curation_system_prompt("en", "Staff Engineer", ["python", "aws"])
    assert "top 2 to 3 most relevant" in prompt
    assert "at most 3 punchy, high-impact bullets" in prompt
    assert "NEVER fabricate, invent, or extrapolate" in prompt
    assert "Portuguese (pt-BR)" not in prompt

    prompt_pt = build_human_curation_system_prompt("pt-br", "Engenheiro Backend", ["python"])
    assert "Portuguese (pt-BR)" in prompt_pt


def test_resume_compiler_condensed_mode():
    spec = ResumeSpec(
        name="Test Candidate",
        title="Software Engineer",
        contact="test@example.com",
        sections=[
            ResumeSection(
                title="Experience",
                blocks=[
                    ResumeBlock(type="subheading", text="Engineer | Acme Corp (2020-2024)"),
                    ResumeBlock(type="bullet", text="Built scalable APIs with FastAPI."),
                ],
            )
        ],
    )
    compiler = ResumeCompiler()
    buf_normal, pages_normal = compiler.compile_pdf(spec, condensed=False)
    assert pages_normal == 1
    assert buf_normal.getbuffer().nbytes > 0

    buf_condensed, pages_condensed = compiler.compile_pdf(spec, condensed=True)
    assert pages_condensed == 1
    assert buf_condensed.getbuffer().nbytes > 0


def test_dual_resume_pipeline_ats_and_human_modes(
    session: Session,
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    _, profile, job = candidate_setup
    update_settings(
        session,
        SettingsUpdate(active_provider="gemini", gemini_api_key="AIzaMockDualKey123"),
    )

    def mock_llm_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        sys_text = body.get("systemInstruction", {}).get("parts", [{}])[0].get("text", "")
        user_text = body.get("contents", [{}])[0].get("parts", [{}])[0].get("text", "")
        all_text = f"{sys_text} {user_text}"
        is_human = "top 2 to 3 most relevant" in all_text or "recruiter-facing" in all_text
        spec_title = "Human Curated Spec" if is_human else "ATS Full Spec"
        payload = {
            "resume_spec": {
                "name": profile.full_name,
                "title": spec_title,
                "contact": "davi@example.com",
                "filename_title": "Resume",
                "sections": [
                    {
                        "title": "Experience",
                        "blocks": [
                            {
                                "type": "subheading",
                                "text": "Software Engineering Intern | Itaú Unibanco (02/2024 - 06/2025)",
                            },
                            {
                                "type": "bullet",
                                "text": "Built Python REST integrations between ServiceNow and AWS S3.",
                            },
                        ],
                    }
                ],
            },
            "cover_letter": "Para 1\n\nPara 2\n\nPara 3",
        }
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]},
        )

    orchestrator = LLMOrchestrator(
        session,
        http_client=httpx.Client(transport=httpx.MockTransport(mock_llm_handler)),
    )

    # 1. Run ATS tailoring
    ats_res = tailor_job_application(session, job, profile, orchestrator=orchestrator, mode="ats")
    assert ats_res.resume_spec.title == "ATS Full Spec"
    assert ats_res.resume_spec_ats is not None
    assert ats_res.resume_spec_ats.title == "ATS Full Spec"
    assert ats_res.resume_spec_human is None

    # Check persistence in database
    artifact = session.exec(
        select(TailoredArtifact).where(TailoredArtifact.job_id == job.id)
    ).first()
    assert artifact is not None
    assert isinstance(artifact.tailored_experience_json, dict)
    assert "ats" in artifact.tailored_experience_json
    assert artifact.tailored_experience_json["ats"]["title"] == "ATS Full Spec"
    assert artifact.tailored_experience_json["human"] is None

    # 2. Run Human tailoring
    human_res = tailor_job_application(
        session, job, profile, orchestrator=orchestrator, mode="human"
    )
    assert human_res.resume_spec.title == "Human Curated Spec"
    assert human_res.resume_spec_human is not None
    assert human_res.resume_spec_human.title == "Human Curated Spec"
    # ATS version must be preserved
    assert human_res.resume_spec_ats is not None
    assert human_res.resume_spec_ats.title == "ATS Full Spec"

    session.refresh(artifact)
    saved_json = artifact.tailored_experience_json
    assert isinstance(saved_json, dict)
    assert isinstance(saved_json.get("ats"), dict)
    assert isinstance(saved_json.get("human"), dict)
    assert saved_json["ats"]["title"] == "ATS Full Spec"
    assert saved_json["human"]["title"] == "Human Curated Spec"

    # 3. get_tailored_artifact returns dual representation
    retrieved = get_tailored_artifact(session, job, profile)
    assert retrieved is not None
    assert retrieved.resume_spec_ats is not None
    assert retrieved.resume_spec_ats.title == "ATS Full Spec"
    assert retrieved.resume_spec_human is not None
    assert retrieved.resume_spec_human.title == "Human Curated Spec"


def test_retrocompatibility_legacy_tailored_experience_json(
    session: Session,
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    _, profile, job = candidate_setup
    legacy_json = {
        "name": profile.full_name,
        "title": "Legacy Direct Spec",
        "contact": "davi@example.com",
        "filename_title": "Resume_Legacy",
        "sections": [],
    }
    assert job.id is not None
    artifact = TailoredArtifact(
        job_id=job.id,
        selected_persona="Backend Engineer",
        tailored_summary="Summary",
        tailored_experience_json=legacy_json,
        cover_letter_text="P1\n\nP2\n\nP3",
    )
    session.add(artifact)
    session.commit()

    retrieved = get_tailored_artifact(session, job, profile)
    assert retrieved is not None
    assert retrieved.resume_spec.title == "Legacy Direct Spec"
    assert retrieved.resume_spec_ats is not None
    assert retrieved.resume_spec_ats.title == "Legacy Direct Spec"
    assert retrieved.resume_spec_human is None


def test_tailor_router_modes_and_validation(
    session: Session,
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    from fastapi.testclient import TestClient

    user, _, job = candidate_setup

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            headers = {"X-User-Id": str(user.id)}

            # Invalid mode should 400
            resp_invalid = client.post(f"/api/tailor/{job.id}?mode=invalid_mode", headers=headers)
            assert resp_invalid.status_code == 400
            assert (
                "Query parameter 'mode' must be 'ats' or 'human'" in resp_invalid.json()["detail"]
            )
    finally:
        app.dependency_overrides.clear()


def test_normalize_cover_letter_word_boundary_split():
    long_single_paragraph = (
        "This is an atypical cover letter paragraph without any periods or exclamation marks "
        "that needs to be split across multiple paragraphs while maintaining word boundaries "
        "and never slicing words down the middle arbitrarily"
    )
    result = _normalize_cover_letter(long_single_paragraph)
    paras = result.split("\n\n")
    assert len(paras) == 3
    for word in long_single_paragraph.split():
        assert any(word in p.split() for p in paras)


def test_guardrail_tolerates_bracketed_contract_subheadings(
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    _, profile, _ = candidate_setup
    spec = ResumeSpec(
        name=profile.full_name or "Candidate",
        title="Software Engineer",
        sections=[
            ResumeSection(
                title="Professional Experience",
                blocks=[
                    ResumeBlock(
                        type="subheading",
                        text="Software Engineering Intern | Itaú Unibanco [Contract] (02/2024 - 06/2025)",
                    ),
                    ResumeBlock(
                        type="bullet",
                        text="Built Python REST integrations syncing 10,000+ records/day.",
                    ),
                ],
            )
        ],
    )
    verify_factual_guardrails(spec, profile)


def test_serialize_profile_includes_is_contract(
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    _, profile, _ = candidate_setup
    assert profile.id is not None
    exp = Experience(
        profile_id=profile.id,
        company="Contractor Corp",
        role="Cloud Consultant",
        is_contract=True,
    )
    data = _serialize_profile_for_prompt(profile, [exp])
    assert "experiences" in data
    assert len(data["experiences"]) == 1
    assert data["experiences"][0]["is_contract"] is True


def test_expand_resume_section_endpoint(
    session: Session,
    candidate_setup: tuple[User, MasterProfile, JobPosting],
):
    from fastapi.testclient import TestClient

    user, _, _ = candidate_setup

    def override_get_db():
        yield session

    class MockOrchestrator:
        def generate(self, prompt: str, system_prompt: str, temperature: float = 0.2) -> str:
            assert "Master Profile data:" in prompt
            assert "Impersonal style" in system_prompt
            return "```markdown\n### Engineering Architecture\n- Architected high throughput pipelines.\n```"

    from backend.app.routers.tailor import get_llm_orchestrator

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_llm_orchestrator] = lambda: MockOrchestrator()
    try:
        with TestClient(app) as client:
            headers = {"X-User-Id": str(user.id)}
            resp = client.post(
                "/api/tailor/expand-section",
                json={"current_text": "# Davi Machado\n## Experience"},
                headers=headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "expanded_text" in data
            assert (
                data["expanded_text"]
                == "### Engineering Architecture\n- Architected high throughput pipelines."
            )
    finally:
        app.dependency_overrides.clear()
