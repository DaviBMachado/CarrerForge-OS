import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.app.db import get_db
from backend.app.services.legacy_importer import parse_markdown_resume
from backend.main import app


@pytest.fixture
def client():
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

    def override_get_db():
        with Session(test_engine) as sess:
            yield sess

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_get_initial_profile(client: TestClient):
    response = client.get("/api/profile")
    assert response.status_code == 200
    data = response.json()
    assert data["full_name"] == "Davi Bernardes Machado"
    assert data["headline"] == "Engenheiro de Software Backend"
    assert "experiences" in data
    assert isinstance(data["experiences"], list)


def test_update_profile(client: TestClient):
    payload = {
        "full_name": "Davi Machado",
        "headline": "Lead AI & Backend Engineer",
        "contact_json": {
            "email": "davi@example.com",
            "phone": "+55 11 99999-9999",
            "location": "São Paulo, Brazil",
        },
        "summary_base": "Backend engineer with track record in Python, AWS and LLM pipelines.",
        "skills_json": {
            "Backend": ["Python", "FastAPI", "SQLModel"],
            "Cloud": ["AWS Lambda", "S3", "Step Functions"],
        },
        "languages_json": ["Portuguese (Native)", "English (Fluent)"],
    }
    response = client.put("/api/profile", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["full_name"] == "Davi Machado"
    assert data["headline"] == "Lead AI & Backend Engineer"
    assert data["contact_json"]["email"] == "davi@example.com"
    assert "Backend" in data["skills_json"]
    assert len(data["languages_json"]) == 2


def test_experience_crud_lifecycle(client: TestClient):
    # 1. Create Experience
    exp_payload = {
        "company": "Itaú Unibanco",
        "role": "Engenheiro de Software Backend Estagiário",
        "location": "São Paulo, SP",
        "start_date": "02/2024",
        "end_date": "06/2025",
        "is_current": False,
        "bullets_json": [
            {
                "text": "Construção de pipelines RAG e integração com LLM para chamados.",
                "tags": ["Python", "LLM", "RAG", "AWS"],
            },
            {
                "text": "Sincronização de 10k+ registros/dia entre ServiceNow e AWS S3.",
                "tags": ["API", "REST", "AWS", "S3"],
            },
        ],
    }
    create_res = client.post("/api/profile/experiences", json=exp_payload)
    assert create_res.status_code == 201
    created = create_res.json()
    exp_id = created["id"]
    assert exp_id is not None
    assert created["company"] == "Itaú Unibanco"
    assert len(created["bullets_json"]) == 2
    assert "RAG" in created["bullets_json"][0]["tags"]

    # 2. Check in GET /api/profile
    profile_res = client.get("/api/profile")
    assert profile_res.status_code == 200
    experiences = profile_res.json()["experiences"]
    assert any(e["id"] == exp_id for e in experiences)

    # 3. Update Experience
    update_payload = {
        "role": "Engenheiro de Software Backend",
        "bullets_json": [
            {
                "text": "Pipelines RAG de alta performance e automações com Step Functions.",
                "tags": ["Python", "RAG", "Step Functions", "AWS"],
            }
        ],
    }
    put_res = client.put(f"/api/profile/experiences/{exp_id}", json=update_payload)
    assert put_res.status_code == 200
    updated = put_res.json()
    assert updated["role"] == "Engenheiro de Software Backend"
    assert len(updated["bullets_json"]) == 1
    assert "Step Functions" in updated["bullets_json"][0]["tags"]

    # 4. Delete Experience
    del_res = client.delete(f"/api/profile/experiences/{exp_id}")
    assert del_res.status_code == 204

    # 5. Verify deletion
    verify_res = client.get("/api/profile")
    assert not any(e["id"] == exp_id for e in verify_res.json()["experiences"])

    # 6. Delete again should return 404
    del_404 = client.delete(f"/api/profile/experiences/{exp_id}")
    assert del_404.status_code == 404


def test_experience_not_found(client: TestClient):
    res_put = client.put("/api/profile/experiences/99999", json={"role": "Lead"})
    assert res_put.status_code == 404

    res_del = client.delete("/api/profile/experiences/99999")
    assert res_del.status_code == 404


def test_legacy_import_endpoint(client: TestClient):
    response = client.post("/api/profile/import-legacy")
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "imported"
    assert result["experiences_count"] == 2
    assert result["total_bullets"] == 12
    assert result["specs_scanned"] == 0

    # Verify profile data populated
    profile_res = client.get("/api/profile")
    assert profile_res.status_code == 200
    p = profile_res.json()
    assert p["full_name"] == "Davi Bernardes Machado"
    assert p["contact_json"]["email"] == "davibernardes021@gmail.com"
    assert len(p["experiences"]) == 2

    # 1st entry: canonical employment experience without spec duplicates
    exp_employment = p["experiences"][0]
    assert exp_employment["company"] == "Itaú Unibanco"
    assert exp_employment["role"] == "Estagiário em Engenharia de Software Backend"
    assert exp_employment["start_date"] == "02/2024"
    assert exp_employment["end_date"] == "06/2025"
    assert len(exp_employment["bullets_json"]) == 8
    first_bullet = exp_employment["bullets_json"][0]
    assert first_bullet["text"].startswith("Agentes conversacionais e IA generativa:")
    assert "Python" in first_bullet["tags"]
    assert "RAG" in first_bullet["tags"]

    # 2nd entry: generically classified as a project, clean year, tech stack converted to tags
    exp_project = p["experiences"][1]
    assert "Projeto" in exp_project["company"]
    assert exp_project["role"] == "ConsultorIA Dores — Agente de Crédito e Chat Conversacional"
    assert exp_project["start_date"] == "2026"
    assert exp_project["end_date"] == "2026"
    assert len(exp_project["bullets_json"]) == 4
    assert not any(
        b["text"].lower().startswith("tecnologias:") for b in exp_project["bullets_json"]
    )
    all_project_tags = {tag for b in exp_project["bullets_json"] for tag in b["tags"]}
    assert {"Python", "LangChain", "FAISS", "Pydantic"}.issubset(all_project_tags)


def test_parse_markdown_resume_generic_project_detection():
    sample_md = """# Maria Silva
São Paulo - SP | maria@example.com

## Experiência Profissional

### Desenvolvedora Backend Pleno | Acme Payments (01/2023 - Atual)
- **Arquitetura de Microsserviços:** desenvolvimento de APIs RESTful em Python com Docker
e PostgreSQL.

### Motor Antifraude — Pipeline de Detecção em Tempo Real (Hackathon FinTech 2024 — 2º Lugar)
- Processamento de eventos de transação com baixa latência em AWS Lambda.
- Stack: Python, AWS, DynamoDB, Docker.
"""
    parsed = parse_markdown_resume(sample_md)
    assert len(parsed["experiences"]) == 2

    role_exp = parsed["experiences"][0]
    assert role_exp["company"] == "Acme Payments"
    assert role_exp["role"] == "Desenvolvedora Backend Pleno"
    assert role_exp["start_date"] == "01/2023"
    assert role_exp["is_current"] is True
    assert role_exp["bullets_json"][0]["text"].startswith("Arquitetura de Microsserviços:")

    proj_exp = parsed["experiences"][1]
    assert proj_exp["company"] == "Acme Payments · Projeto (Hackathon FinTech 2024 — 2º Lugar)"
    assert proj_exp["role"] == "Motor Antifraude — Pipeline de Detecção em Tempo Real"
    assert proj_exp["start_date"] == "2024"
    assert proj_exp["end_date"] == "2024"
    assert len(proj_exp["bullets_json"]) == 1
    assert "DynamoDB" in proj_exp["bullets_json"][0]["tags"]


def test_multi_user_isolation_and_idor_prevention(client: TestClient):
    # 1. User 1 creates an experience
    h1 = {"Authorization": "Bearer 1"}
    h2 = {"Authorization": "Bearer 2"}

    create_resp = client.post(
        "/api/profile/experiences",
        headers=h1,
        json={
            "company": "User 1 Company",
            "role": "Lead Architect",
            "bullets_json": [{"text": "Core platform design", "tags": ["python"]}],
        },
    )
    assert create_resp.status_code == 201
    exp_id = create_resp.json()["id"]

    # 2. User 2 tries to update User 1's experience (IDOR attempt)
    update_resp = client.put(
        f"/api/profile/experiences/{exp_id}",
        headers=h2,
        json={"company": "Hacked Company"},
    )
    assert update_resp.status_code == 404
    assert update_resp.json()["detail"] == "Experience not found"

    # 3. User 2 tries to delete User 1's experience (IDOR attempt)
    delete_resp = client.delete(
        f"/api/profile/experiences/{exp_id}",
        headers=h2,
    )
    assert delete_resp.status_code == 404
    assert delete_resp.json()["detail"] == "Experience not found"

    # 4. User 2 verifies their own profile does not contain User 1's experience
    u2_profile = client.get("/api/profile", headers=h2).json()
    u2_exp_ids = [e["id"] for e in u2_profile["experiences"]]
    assert exp_id not in u2_exp_ids

    # 5. User 1 can legitimately update and delete their own experience
    valid_update = client.put(
        f"/api/profile/experiences/{exp_id}",
        headers=h1,
        json={"company": "User 1 Updated Company"},
    )
    assert valid_update.status_code == 200
    assert valid_update.json()["company"] == "User 1 Updated Company"

    valid_delete = client.delete(f"/api/profile/experiences/{exp_id}", headers=h1)
    assert valid_delete.status_code == 204


def test_require_auth_flag_blocks_unauthenticated_requests(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CAREERFORGE_REQUIRE_AUTH", "true")

    # Unauthenticated request must receive 401
    unauth_resp = client.get("/api/profile")
    assert unauth_resp.status_code == 401
    assert unauth_resp.json()["detail"] == "Authentication required"

    # Authenticated request succeeds
    auth_resp = client.get("/api/profile", headers={"Authorization": "Bearer 1"})
    assert auth_resp.status_code == 200
