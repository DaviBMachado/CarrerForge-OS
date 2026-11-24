import asyncio
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from backend.app.models import Experience, JobPosting, MasterProfile, TailoredArtifact, User
from backend.app.services.scrapers.base import ScrapedJob
from backend.app.services.scrapers.deduplicator import compute_job_hash, upsert_scraped_jobs
from backend.app.services.scrapers.gupy import parse_gupy_html, scrape_gupy_jobs
from backend.app.services.scrapers.linkedin import parse_linkedin_html, scrape_linkedin_jobs
from backend.app.services.scrapers.runner import (
    MAX_STORED_RUNS,
    RUNS,
    ScraperRun,
    evict_old_runs,
    stop_scraper_run,
    stream_run_events,
)
from backend.main import app

LINKEDIN_SAMPLE_HTML = """
<ul class="jobs-search__results-list">
  <li data-occludable-job-id="3812345678">
    <div class="base-search-card" data-entity-urn="urn:li:jobPosting:3812345678">
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/3812345678/?trk=public_jobs">
        Senior Backend Engineer
      </a>
      <h3 class="base-search-card__title">Senior Backend Engineer</h3>
      <h4 class="base-search-card__subtitle">
        <a class="hidden-nested-link">Acme Fintech</a>
      </h4>
      <span class="job-search-card__location">São Paulo, Brazil (Remote)</span>
      <span class="job-search-card__employment-type">Full-time</span>
      <div class="show-more-less-html__markup">
        We need a Python and FastAPI specialist with distributed systems experience.
      </div>
    </div>
  </li>
  <li data-occludable-job-id="3899999999">
    <div class="base-search-card" data-entity-urn="urn:li:jobPosting:3899999999">
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/3899999999/">
        Staff Software Engineer
      </a>
      <h3 class="base-search-card__title">Staff Software Engineer</h3>
      <h4 class="base-search-card__subtitle">CloudCorp</h4>
      <span class="job-search-card__location">Remote</span>
      <span class="job-search-card__employment-type">Contract</span>
      <div class="jobs-description__content">
        Build resilient cloud infrastructure with Kubernetes and Go.
      </div>
    </div>
  </li>
</ul>
"""

GUPY_SAMPLE_HTML = """
<div class="gupy-job-list">
  <article class="job-card" data-job-id="gupy-778899">
    <a class="job-link" href="https://acme.gupy.io/jobs/778899">
      <h2 class="job-title">Desenvolvedor(a) Python Sênior</h2>
    </a>
    <span class="company-name">Acme Brasil</span>
    <span class="job-location">Remoto</span>
    <div class="job-description">
      Atuar no desenvolvimento de APIs REST escaláveis em Python.
    </div>
    <div class="job-requirements">
      Requisitos: Python 3.11+, FastAPI, SQL, Docker.
    </div>
  </article>
  <article class="job-card" data-job-id="gupy-112233">
    <a class="job-link" href="https://beta.gupy.io/jobs/112233">
      <h2 class="job-title">Tech Lead Backend</h2>
    </a>
    <span class="company-name">Beta Pagamentos</span>
    <span class="job-location">São Paulo - Híbrido</span>
    <div class="job-description">
      Liderar time de engenharia em arquitetura de microsserviços.
    </div>
    <div class="job-requirements">
      Experiência com sistemas críticos de alta disponibilidade.
    </div>
  </article>
</div>
"""


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


LINKEDIN_AUTH_SAMPLE_HTML = """
<ul class="jobs-search-results__list">
  <li class="jobs-search-results__list-item" data-occludable-job-id="55667788">
    <div class="job-card-container">
      <a class="job-card-list__title" href="https://www.linkedin.com/jobs/view/55667788/">
        Lead Systems Engineer
      </a>
      <span class="job-card-container__primary-description">Innovate AI</span>
      <span class="job-card-container__metadata-item">Remote, Brazil</span>
      <div class="jobs-description__content">Architect scalable distributed services.</div>
    </div>
  </li>
</ul>
"""


def test_parse_linkedin_html_extracts_fields():
    jobs = parse_linkedin_html(LINKEDIN_SAMPLE_HTML)
    assert len(jobs) == 2

    first = jobs[0]
    assert first.platform == "linkedin"
    assert first.external_id == "3812345678"
    assert first.title == "Senior Backend Engineer"
    assert first.company == "Acme Fintech"
    assert first.location == "São Paulo, Brazil (Remote)"
    assert first.employment_type == "Full-time"
    assert "Python and FastAPI specialist" in first.description
    assert first.url == "https://www.linkedin.com/jobs/view/3812345678/"


def test_parse_linkedin_authenticated_html():
    jobs = parse_linkedin_html(LINKEDIN_AUTH_SAMPLE_HTML)
    assert len(jobs) == 1
    first = jobs[0]
    assert first.platform == "linkedin"
    assert first.external_id == "55667788"
    assert first.title == "Lead Systems Engineer"
    assert first.company == "Innovate AI"
    assert "Remote" in (first.location or "")
    assert first.remote_type == "remote"
    assert "Architect scalable distributed services" in first.description
    assert first.url == "https://www.linkedin.com/jobs/view/55667788/"


def test_parse_linkedin_flagship3_componentkey_html():
    sample_html = """
    <div data-testid="lazy-column">
      <div componentkey="job-card-component-ref-99887766">
        <div>
          <span aria-hidden="true">Staff Backend Engineer</span>
          <p class="company">FactSet Corp</p>
          <p class="location">São Paulo, SP (Remoto)</p>
          <p class="status">Visto</p>
        </div>
      </div>
      <div componentkey="job-card-component-ref-11223344">
        <div>
          <span aria-hidden="true">Senior Python Architect</span>
          <p class="company">CloudTech Solutions</p>
          <p class="location">São Paulo, SP</p>
        </div>
      </div>
    </div>
    """
    jobs = parse_linkedin_html(sample_html)
    assert len(jobs) == 2
    j1 = jobs[0]
    assert j1.external_id == "99887766"
    assert j1.title == "Staff Backend Engineer"
    assert j1.company == "FactSet Corp"
    assert "Remoto" in (j1.location or "")
    assert j1.remote_type == "remote"
    assert j1.url == "https://www.linkedin.com/jobs/view/99887766/"

    j2 = jobs[1]
    assert j2.external_id == "11223344"
    assert j2.title == "Senior Python Architect"
    assert j2.company == "CloudTech Solutions"
    assert j2.url == "https://www.linkedin.com/jobs/view/11223344/"


def test_parse_gupy_html_extracts_fields_and_requirements():
    jobs = parse_gupy_html(GUPY_SAMPLE_HTML)
    assert len(jobs) == 2

    first = jobs[0]
    assert first.platform == "gupy"
    assert first.external_id == "gupy-778899"
    assert first.title == "Desenvolvedor(a) Python Sênior"
    assert first.company == "Acme Brasil"
    assert "APIs REST escaláveis" in first.description
    assert "Python 3.11+, FastAPI" in (first.requirements or "")
    assert first.url == "https://acme.gupy.io/jobs/778899"


def test_linkedin_scraper_respects_batch_cap_and_jitter():
    cards_html = "\n".join(
        f"""
        <li data-occludable-job-id="job-{i}">
          <div class="base-search-card" data-entity-urn="urn:li:jobPosting:job-{i}">
            <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/job-{i}/">
              Engineer {i}
            </a>
            <h3 class="base-search-card__title">Engineer {i}</h3>
            <h4 class="base-search-card__subtitle">Company {i}</h4>
            <span class="job-search-card__location">Remote</span>
            <span class="job-search-card__employment-type">Full-time</span>
            <div class="show-more-less-html__markup">Description for job {i}</div>
          </div>
        </li>
        """
        for i in range(40)
    )
    full_html = f"<ul>{cards_html}</ul>"

    mock_page = AsyncMock()
    mock_page.content = AsyncMock(return_value=full_html)
    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_context

    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    with (
        patch(
            "backend.app.services.scrapers.linkedin.get_authenticated_context",
            return_value=mock_cm,
        ),
        patch("backend.app.services.scrapers.linkedin.asyncio.sleep", side_effect=fake_sleep),
    ):
        results = asyncio.run(
            scrape_linkedin_jobs(
                query="Python",
                location="Brazil",
                remote=True,
                max_jobs=25,
            )
        )

    assert len(results) == 25
    assert len(delays) >= 1
    assert all(2.0 <= d <= 5.0 for d in delays)


def test_gupy_scraper_extracts_jobs():
    mock_page = AsyncMock()
    mock_page.content = AsyncMock(return_value=GUPY_SAMPLE_HTML)
    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_context

    with (
        patch(
            "backend.app.services.scrapers.gupy.get_authenticated_context",
            return_value=mock_cm,
        ),
        patch("backend.app.services.scrapers.gupy.asyncio.sleep", new_callable=AsyncMock),
    ):
        results = asyncio.run(scrape_gupy_jobs(query="Python", max_jobs=10))

    assert len(results) == 2
    assert results[0].title == "Desenvolvedor(a) Python Sênior"


def test_deduplication_composite_hash_and_upsert_preserves_tailored_drafts(session: Session):
    user = User(email="scraper@careerforge.local")
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.id is not None

    hash_id_1 = compute_job_hash(platform="linkedin", external_id="3812345678")
    hash_id_2 = compute_job_hash(platform="linkedin", external_id="3812345678")
    assert hash_id_1 == hash_id_2

    hash_title_1 = compute_job_hash(
        platform="gupy",
        external_id=None,
        company="Acme Corp",
        title="  Senior   Python Developer! ",
    )
    hash_title_2 = compute_job_hash(
        platform="gupy",
        external_id=None,
        company="acme corp",
        title="senior python developer",
    )
    assert hash_title_1 == hash_title_2

    initial_jobs = parse_linkedin_html(LINKEDIN_SAMPLE_HTML)
    saved_first = upsert_scraped_jobs(session, user.id, initial_jobs)
    assert len(saved_first) == 2

    # Simulate user tailoring the first job (changing status and attaching TailoredArtifact)
    job_1 = saved_first[0]
    assert job_1.id is not None
    job_1.status = "tailored"
    session.add(job_1)
    artifact = TailoredArtifact(
        job_id=job_1.id,
        selected_persona="Backend Specialist",
        tailored_summary="Custom tailored summary that must not be lost",
    )
    session.add(artifact)
    session.commit()

    # Re-scrape the exact same jobs + 1 job with missing external_id deduplicated by company+title
    duplicate_jobs = parse_linkedin_html(LINKEDIN_SAMPLE_HTML)
    duplicate_jobs[0].description = "Updated description text from re-crawl"

    saved_second = upsert_scraped_jobs(session, user.id, duplicate_jobs)
    assert len(saved_second) == 2

    all_db_jobs = session.exec(select(JobPosting).where(JobPosting.user_id == user.id)).all()
    assert len(all_db_jobs) == 2

    refreshed_job_1 = session.get(JobPosting, job_1.id)
    assert refreshed_job_1 is not None
    assert refreshed_job_1.status == "tailored"
    assert len(refreshed_job_1.artifacts) == 1
    assert (
        refreshed_job_1.artifacts[0].tailored_summary
        == "Custom tailored summary that must not be lost"
    )


def test_scraper_runner_start_and_sse_stream():
    RUNS.clear()
    fake_job = ScrapedJob(
        platform="linkedin",
        external_id="li-999",
        title="Async Python Dev",
        company="StreamCorp",
        description="FastAPI SSE testing",
    )

    async def fake_linkedin_scrape(**kwargs):
        if kwargs.get("on_log"):
            kwargs["on_log"]("Navigating to page 2...")
            kwargs["on_log"]("Scraped 15 jobs...")
        return [fake_job]

    with (
        patch(
            "backend.app.services.scrapers.runner.scrape_linkedin_jobs",
            side_effect=fake_linkedin_scrape,
        ),
        patch(
            "backend.app.services.scrapers.runner.scrape_gupy_jobs",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "backend.app.services.scrapers.runner.upsert_scraped_jobs",
            return_value=[],
        ),
        TestClient(app) as client,
    ):
        start_resp = client.post(
            "/api/scraper/start",
            json={"query": "Python", "location": "Remote", "platforms": ["linkedin"]},
        )
        assert start_resp.status_code == 200
        run_id = start_resp.json()["run_id"]
        assert isinstance(run_id, str) and len(run_id) > 0

        stream_resp = client.get(f"/api/scraper/stream/{run_id}")
        assert stream_resp.status_code == 200
        assert "text/event-stream" in stream_resp.headers["content-type"]

        events = [
            json.loads(line.removeprefix("data: "))
            for line in stream_resp.text.splitlines()
            if line.startswith("data: ")
        ]
        messages = [e["message"] for e in events]
        assert any("Navigating to page 2..." in m for m in messages)
        assert any("Scraped 15 jobs..." in m for m in messages)
        assert messages[-1] == "Done."
        assert events[-1]["progress"] == 100
        assert events[-1]["status"] == "completed"


def test_scraper_runner_stop_cancels_active_task():
    RUNS.clear()
    cancelled = False

    async def slow_linkedin_scrape(**kwargs):
        nonlocal cancelled
        try:
            await asyncio.sleep(30)
            return []
        except asyncio.CancelledError:
            cancelled = True
            raise

    async def run_cancellation_flow():
        with patch(
            "backend.app.services.scrapers.runner.scrape_linkedin_jobs",
            side_effect=slow_linkedin_scrape,
        ):
            from backend.app.services.scrapers.runner import start_scraper_run

            run = await start_scraper_run(query="Python", platforms=["linkedin"])
            await asyncio.sleep(0.05)
            assert not run.done

            stopped = await stop_scraper_run(run.run_id)
            assert stopped is not None
            assert stopped.status == "stopped"
            assert stopped.done is True
            assert cancelled is True

            chunks = [chunk async for chunk in stream_run_events(run.run_id)]
            assert any("Scraper aborted." in c for c in chunks)

    asyncio.run(run_cancellation_flow())


def test_scraper_jobs_list_and_archive_status_update():
    with TestClient(app) as client:
        # Ensure default profile/user exists and insert a scraped job
        initial_resp = client.get("/api/scraper/jobs")
        assert initial_resp.status_code == 200

        suffix = uuid.uuid4().hex[:8]
        ext_id = f"gupy-radar-{suffix}"
        fake_job = ScrapedJob(
            platform="gupy",
            external_id=ext_id,
            title=f"Fullstack Engineer {suffix}",
            company=f"RadarTech {suffix}",
            location="São Paulo, SP",
            description="React and FastAPI role",
            url="https://radartech.gupy.io/jobs/1",
        )

        async def fake_gupy_scrape(**kwargs):
            return [fake_job]

        with (
            patch(
                "backend.app.services.scrapers.runner.scrape_gupy_jobs",
                side_effect=fake_gupy_scrape,
            ),
            patch(
                "backend.app.services.scrapers.runner.scrape_linkedin_jobs",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            start_resp = client.post(
                "/api/scraper/start",
                json={"query": "React", "location": "São Paulo", "platforms": ["gupy"]},
            )
            assert start_resp.status_code == 200
            run_id = start_resp.json()["run_id"]
            stream_resp = client.get(f"/api/scraper/stream/{run_id}")
            _ = stream_resp.text

        jobs_resp = client.get("/api/scraper/jobs")
        assert jobs_resp.status_code == 200
        jobs = jobs_resp.json()
        target = next((j for j in jobs if j["external_id"] == ext_id), None)
        assert target is not None
        assert target["title"] == f"Fullstack Engineer {suffix}"
        assert target["status"] == "discovered"

        patch_resp = client.patch(
            f"/api/scraper/jobs/{target['id']}/status",
            json={"status": "archived"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["status"] == "archived"


def test_scraper_tenant_isolation_and_ownership():
    with TestClient(app) as client:
        suffix = uuid.uuid4().hex[:8]
        ext_id = f"gupy-tenant2-{suffix}"
        fake_job = ScrapedJob(
            platform="gupy",
            external_id=ext_id,
            title=f"Tenant2 Engineer {suffix}",
            company="MultiTenant Corp",
            location="Remote",
            description="Tenant isolation check",
            url="https://tenant.gupy.io/jobs/2",
        )

        async def fake_gupy_scrape(**kwargs):
            return [fake_job]

        with (
            patch(
                "backend.app.services.scrapers.runner.scrape_gupy_jobs",
                side_effect=fake_gupy_scrape,
            ),
            patch(
                "backend.app.services.scrapers.runner.scrape_linkedin_jobs",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            # User 2 initiates scraper run
            start_resp = client.post(
                "/api/scraper/start",
                headers={"X-User-Id": "2"},
                json={"query": "Tenant2", "platforms": ["gupy"]},
            )
            assert start_resp.status_code == 200
            run_id = start_resp.json()["run_id"]
            client.get(f"/api/scraper/stream/{run_id}")

        # User 2 lists jobs: must contain the scraped job
        resp_u2 = client.get("/api/scraper/jobs", headers={"X-User-Id": "2"})
        assert resp_u2.status_code == 200
        jobs_u2 = resp_u2.json()
        target_u2 = next((j for j in jobs_u2 if j["external_id"] == ext_id), None)
        assert target_u2 is not None
        assert target_u2["user_id"] == 2

        # User 1 (or default user) lists jobs: must NOT contain user 2's job
        resp_u1 = client.get("/api/scraper/jobs", headers={"X-User-Id": "1"})
        assert resp_u1.status_code == 200
        jobs_u1 = resp_u1.json()
        assert not any(j["external_id"] == ext_id for j in jobs_u1)

        # User 1 attempts to update User 2's job: returns 404
        patch_resp = client.patch(
            f"/api/scraper/jobs/{target_u2['id']}/status",
            headers={"X-User-Id": "1"},
            json={"status": "archived"},
        )
        assert patch_resp.status_code == 404


def test_scraper_runs_eviction_lifecycle():
    RUNS.clear()

    # Pre-populate RUNS with 10 completed runs
    for i in range(10):
        r_id = f"completed-run-{i}"
        RUNS[r_id] = ScraperRun(run_id=r_id, done=True, status="completed")

    assert len(RUNS) == MAX_STORED_RUNS

    # Add an 11th run and invoke eviction to target capacity MAX_STORED_RUNS
    r11_id = "completed-run-11"
    RUNS[r11_id] = ScraperRun(run_id=r11_id, done=True, status="completed")
    evict_old_runs(MAX_STORED_RUNS)

    assert len(RUNS) == MAX_STORED_RUNS
    # Oldest run should have been evicted
    assert "completed-run-0" not in RUNS
    # Newest run must still exist
    assert "completed-run-11" in RUNS


def test_active_runs_protected_from_eviction():
    RUNS.clear()

    # 5 active runs, 5 completed runs
    for i in range(5):
        active_id = f"active-{i}"
        RUNS[active_id] = ScraperRun(run_id=active_id, done=False, status="running")

    for i in range(5):
        done_id = f"done-{i}"
        RUNS[done_id] = ScraperRun(run_id=done_id, done=True, status="completed")

    assert len(RUNS) == 10

    # Evict to capacity 6: should only evict from done runs
    evict_old_runs(target_cap=6)

    assert len(RUNS) == 6
    # All 5 active runs must still be present
    for i in range(5):
        assert f"active-{i}" in RUNS
    # 4 done runs evicted, 1 remaining
    assert sum(1 for r in RUNS.values() if r.done) == 1


def test_upsert_scraped_jobs_calculates_and_persists_match_percentage(session: Session):
    user = User(email="match_user@example.com")
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.id is not None

    # Profile with python and fastapi
    profile = MasterProfile(
        user_id=user.id,
        full_name="Match Tester",
        headline="Python Backend Developer",
        skills_json={"Backend": ["Python", "FastAPI"]},
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    assert profile.id is not None

    exp = Experience(
        user_id=user.id,
        profile_id=profile.id,
        company="TechCorp",
        role="Backend Engineer",
        bullets_json=[{"text": "Engineered REST APIs using Python and FastAPI"}],
    )
    session.add(exp)
    session.commit()

    # Job A requires Python and FastAPI (100% match)
    job_a = ScrapedJob(
        platform="linkedin",
        external_id="match-100",
        title="Python Engineer",
        company="PyCorp",
        description="We require Python and FastAPI for microservices.",
    )

    # Job B requires Python, FastAPI, Kubernetes, and Go (50% match: 2 out of 4)
    # wait: Go isn't in vocab, 'golang' and 'docker' and 'kubernetes' are
    job_b = ScrapedJob(
        platform="gupy",
        external_id="match-50",
        title="Senior Cloud Engineer",
        company="CloudCorp",
        description="We require Python, FastAPI, Docker, and Kubernetes.",
    )

    saved = upsert_scraped_jobs(session, user.id, [job_a, job_b])
    assert len(saved) == 2

    saved_a = next(j for j in saved if j.external_id == "match-100")
    saved_b = next(j for j in saved if j.external_id == "match-50")

    assert saved_a.match_percentage == 100
    assert saved_b.match_percentage == 50


def test_is_job_url_detection():
    from backend.app.services.scrapers.single_extractor import is_job_url

    assert is_job_url("https://www.linkedin.com/jobs/view/123456789") is True
    assert is_job_url("http://empresa.gupy.io/jobs/98765") is True
    assert is_job_url("https://careers.google.com/jobs/results/123") is True
    assert is_job_url("Python Backend Engineer") is False
    assert is_job_url("São Paulo, Brazil") is False
    assert is_job_url("") is False


def test_extract_job_from_url_endpoint():
    fake_job = ScrapedJob(
        platform="linkedin",
        external_id="ext-url-123",
        title="Direct Link Engineer",
        company="DirectCorp",
        location="Remote",
        remote_type="remote",
        description="Extracted directly from URL",
        url="https://www.linkedin.com/jobs/view/ext-url-123/",
    )

    with (
        TestClient(app) as client,
        patch(
            "backend.app.routers.scraper.scrape_job_by_url",
            new_callable=AsyncMock,
            return_value=fake_job,
        ),
    ):
        res = client.post(
            "/api/scraper/extract-url",
            headers={"X-User-Id": "10"},
            json={"url": "https://www.linkedin.com/jobs/view/ext-url-123/"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["title"] == "Direct Link Engineer"
        assert data["company"] == "DirectCorp"
        assert data["user_id"] == 10
        assert data["external_id"] == "ext-url-123"


def test_extract_job_from_url_failure_returns_helpful_message():
    with (
        TestClient(app) as client,
        patch(
            "backend.app.routers.scraper.scrape_job_by_url",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Authwall encountered"),
        ),
    ):
        res = client.post(
            "/api/scraper/extract-url",
            headers={"X-User-Id": "10"},
            json={"url": "https://www.linkedin.com/jobs/view/blocked-job/"},
        )
        assert res.status_code == 400
        data = res.json()
        assert "Não foi possível extrair a vaga do link" in data["detail"]
        assert "Estúdio de Personalização" in data["detail"]


def test_create_manual_job_endpoint_and_tenant_isolation():
    with TestClient(app) as client:
        # User 1 creates manual job
        res_u1 = client.post(
            "/api/scraper/jobs",
            headers={"X-User-Id": "20"},
            json={
                "title": "Staff Backend Architect",
                "company": "Custom Enterprises",
                "location": "São Paulo (Hybrid)",
                "remote_type": "hybrid",
                "raw_description": "We need a specialist in Python and microservices.",
            },
        )
        assert res_u1.status_code == 201
        job_u1 = res_u1.json()
        assert job_u1["title"] == "Staff Backend Architect"
        assert job_u1["company"] == "Custom Enterprises"
        assert job_u1["platform"] == "manual"
        assert job_u1["status"] == "discovered"
        assert job_u1["user_id"] == 20
        assert job_u1["id"] is not None

        # User 2 lists jobs: must not see User 1's manual job
        res_list_u2 = client.get("/api/scraper/jobs", headers={"X-User-Id": "21"})
        assert res_list_u2.status_code == 200
        jobs_u2 = res_list_u2.json()
        assert not any(j["id"] == job_u1["id"] for j in jobs_u2)

        # User 1 lists jobs: sees the manual job
        res_list_u1 = client.get("/api/scraper/jobs", headers={"X-User-Id": "20"})
        assert res_list_u1.status_code == 200
        jobs_u1 = res_list_u1.json()
        assert any(j["id"] == job_u1["id"] for j in jobs_u1)


def test_create_manual_job_validation_errors():
    with TestClient(app) as client:
        # Missing title
        res_no_title = client.post(
            "/api/scraper/jobs",
            headers={"X-User-Id": "20"},
            json={"title": "  ", "company": "Acme", "raw_description": "Description"},
        )
        assert res_no_title.status_code in (400, 422)

        # Missing company
        res_no_comp = client.post(
            "/api/scraper/jobs",
            headers={"X-User-Id": "20"},
            json={"title": "Dev", "company": "", "raw_description": "Description"},
        )
        assert res_no_comp.status_code in (400, 422)

        # Missing description
        res_no_desc = client.post(
            "/api/scraper/jobs",
            headers={"X-User-Id": "20"},
            json={"title": "Dev", "company": "Acme", "raw_description": "   "},
        )
        assert res_no_desc.status_code in (400, 422)

