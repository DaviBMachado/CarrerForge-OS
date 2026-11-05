import json

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.app.models import Experience, MasterProfile, User
from backend.app.schemas.settings import SettingsUpdate
from backend.app.services.llm_orchestrator import (
    LLMConfigurationError,
    LLMOrchestrator,
    LLMProviderError,
    detect_language,
    select_persona,
)
from backend.app.services.settings import update_settings


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


def test_detect_language_distinguishes_en_and_pt_br():
    pt_job = (
        "Buscamos uma pessoa Desenvolvedora Backend Sênior para atuar com Python, AWS e "
        "arquitetura de microsserviços. Requisitos: experiência com bancos de dados relacionais, "
        "integração contínua e boas práticas de engenharia de software em São Paulo."
    )
    en_job = (
        "We are looking for a Senior Backend Engineer to build scalable microservices with "
        "Python and AWS. Requirements: strong experience with relational databases, CI/CD "
        "pipelines, and distributed systems architecture."
    )
    assert detect_language(pt_job) == "pt-br"
    assert detect_language(en_job) == "en"
    assert detect_language("") == "en"


def test_select_persona_evaluates_job_and_profile(session: Session):
    user = User(email="candidate@example.com")
    session.add(user)
    session.flush()
    assert user.id is not None

    profile = MasterProfile(
        user_id=user.id,
        full_name="Davi Machado",
        headline="Software Engineer",
        skills_json={"AI": ["LLM", "RAG", "LangChain"], "Backend": ["Python", "FastAPI", "AWS"]},
    )
    session.add(profile)
    session.flush()
    assert profile.id is not None

    session.add(
        Experience(
            profile_id=profile.id,
            company="Acme",
            role="Software Engineer",
            bullets_json=[
                {"text": "Built RAG pipelines with LangChain and LLMs", "tags": ["LLM", "RAG"]},
                {"text": "Developed Python REST APIs on AWS Lambda", "tags": ["Python", "AWS"]},
            ],
        )
    )
    session.commit()
    session.refresh(profile)

    ai_job = (
        "Looking for an engineer experienced with GenAI, LLM orchestration, RAG pipelines, "
        "LangChain, prompt engineering, and AI agents."
    )
    backend_job = (
        "Procuramos Desenvolvedor Backend Python com experiência em FastAPI, Django, "
        "APIs RESTful, PostgreSQL, microsserviços e arquitetura distribuída."
    )
    cloud_job = (
        "Cloud Infrastructure Engineer needed for AWS Lambda, Terraform, Step Functions, "
        "Docker, CI/CD, and serverless observability."
    )
    fullstack_job = (
        "Fullstack Developer working with React, TypeScript, Angular, frontend components, "
        "and Node/Python web applications."
    )

    assert select_persona(ai_job, profile) == "AI Automation Engineer"
    assert select_persona(backend_job, profile) == "Backend Python Engineer"
    assert select_persona(cloud_job, profile) == "Cloud & Serverless Engineer"
    assert select_persona(fullstack_job, profile) == "Full-Stack Software Engineer"


def test_orchestrator_routes_to_gemini_openai_claude_and_ollama(session: Session):
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        url = str(request.url)
        if "generativelanguage.googleapis.com" in url:
            return httpx.Response(
                200,
                json={
                    "candidates": [{"content": {"parts": [{"text": '{"result": "from_gemini"}'}]}}]
                },
            )
        if "api.openai.com" in url:
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '```json\n{"result": "from_openai"}\n```'}}]
                },
            )
        if "api.anthropic.com" in url:
            return httpx.Response(
                200,
                json={"content": [{"type": "text", "text": '{"result": "from_claude"}'}]},
            )
        if "localhost:11434" in url:
            return httpx.Response(
                200,
                json={"message": {"content": '{"result": "from_ollama"}'}},
            )
        return httpx.Response(404, json={"error": "not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    # 1. Gemini
    update_settings(
        session,
        SettingsUpdate(
            active_provider="gemini",
            gemini_api_key="AIzaSecretGeminiKey999",
            openai_api_key="sk-proj-OpenAISecretKey999",
            anthropic_api_key="sk-ant-ClaudeSecretKey999",
            ollama_base_url="http://localhost:11434",
        ),
    )
    orchestrator = LLMOrchestrator(session, http_client=client)
    assert orchestrator.generate_json("Hello Gemini", system_prompt="Be concise") == {
        "result": "from_gemini"
    }
    assert captured_requests[-1].headers.get("x-goog-api-key") == "AIzaSecretGeminiKey999"

    # 2. OpenAI
    update_settings(session, SettingsUpdate(active_provider="openai"))
    assert orchestrator.generate_json("Hello OpenAI", system_prompt="Be concise") == {
        "result": "from_openai"
    }
    assert captured_requests[-1].headers.get("authorization") == "Bearer sk-proj-OpenAISecretKey999"

    # 3. Claude
    update_settings(session, SettingsUpdate(active_provider="claude"))
    assert orchestrator.generate_json("Hello Claude", system_prompt="Be concise") == {
        "result": "from_claude"
    }
    assert captured_requests[-1].headers.get("x-api-key") == "sk-ant-ClaudeSecretKey999"

    # 4. Ollama
    update_settings(session, SettingsUpdate(active_provider="ollama"))
    assert orchestrator.generate_json("Hello Ollama", system_prompt="Be concise") == {
        "result": "from_ollama"
    }
    body = json.loads(captured_requests[-1].content.decode("utf-8"))
    assert body["format"] == "json"


def test_orchestrator_missing_key_and_error_redaction(session: Session):
    update_settings(session, SettingsUpdate(active_provider="openai"))
    orchestrator = LLMOrchestrator(session)

    with pytest.raises(LLMConfigurationError, match="openai"):
        orchestrator.generate("Test missing key")

    secret_key = "AIzaSuperSensitiveSecretKey7777"
    update_settings(
        session,
        SettingsUpdate(active_provider="gemini", gemini_api_key=secret_key),
    )

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"Unauthorized key {secret_key}")

    fail_client = httpx.Client(transport=httpx.MockTransport(failing_handler))
    failing_orchestrator = LLMOrchestrator(session, http_client=fail_client)

    with pytest.raises(LLMProviderError) as exc_info:
        failing_orchestrator.generate("Trigger failure")

    assert secret_key not in str(exc_info.value)
