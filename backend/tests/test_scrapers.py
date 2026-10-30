import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from backend.app.models import JobPosting, TailoredArtifact, User
from backend.app.services.scrapers.deduplicator import compute_job_hash, upsert_scraped_jobs
from backend.app.services.scrapers.gupy import parse_gupy_html, scrape_gupy_jobs
from backend.app.services.scrapers.linkedin import parse_linkedin_html, scrape_linkedin_jobs

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
