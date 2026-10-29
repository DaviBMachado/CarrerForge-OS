from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["app"] == "CareerForge OS"
    assert "version" in data


def test_api_not_found():
    response = client.get("/api/unknown")
    assert response.status_code == 404


def test_root_fallback_when_dist_empty():
    response = client.get("/")
    assert response.status_code == 200
