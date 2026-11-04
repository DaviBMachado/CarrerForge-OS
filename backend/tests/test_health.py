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


def test_static_and_spa_fallback(monkeypatch, tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    index_html = static_dir / "index.html"
    html_content = "<!DOCTYPE html><html><body>CareerForge SPA</body></html>"
    index_html.write_text(html_content, encoding="utf-8")

    asset_dir = static_dir / "assets"
    asset_dir.mkdir()
    asset_js = asset_dir / "main.js"
    asset_js.write_text("console.log('loaded');", encoding="utf-8")

    import backend.main

    monkeypatch.setattr(backend.main, "STATIC_DIR", static_dir)

    # Root serves index.html
    root_res = client.get("/")
    assert root_res.status_code == 200
    assert "CareerForge SPA" in root_res.text

    # Client-side SPA route falls back to index.html
    spa_res = client.get("/pipeline")
    assert spa_res.status_code == 200
    assert "CareerForge SPA" in spa_res.text

    # Static asset file is served directly
    asset_res = client.get("/assets/main.js")
    assert asset_res.status_code == 200
    assert "console.log" in asset_res.text

    # API routes return 404, not falling back to index.html
    api_res = client.get("/api/nonexistent")
    assert api_res.status_code == 404
