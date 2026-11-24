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
    assert data["full_name"] == ""
    assert data["headline"] is None
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
