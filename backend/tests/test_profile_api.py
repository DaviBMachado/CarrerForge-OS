import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.app.db import get_db
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
    assert result["experiences_count"] >= 1
    assert result["total_bullets"] > 0
    assert result["specs_scanned"] >= 1

    # Verify profile data populated
    profile_res = client.get("/api/profile")
    assert profile_res.status_code == 200
    p = profile_res.json()
    assert p["full_name"] == "Davi Bernardes Machado"
    assert p["contact_json"]["email"] == "davibernardes021@gmail.com"
    assert len(p["experiences"]) >= 1
    assert len(p["experiences"][0]["bullets_json"]) > 0
    # Check that tags are populated
    first_bullet = p["experiences"][0]["bullets_json"][0]
    assert "text" in first_bullet
    assert isinstance(first_bullet["tags"], list)
