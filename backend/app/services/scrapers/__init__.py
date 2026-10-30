from backend.app.services.scrapers.base import ScrapedJob
from backend.app.services.scrapers.deduplicator import compute_job_hash, upsert_scraped_jobs
from backend.app.services.scrapers.gupy import parse_gupy_html, scrape_gupy_jobs
from backend.app.services.scrapers.linkedin import parse_linkedin_html, scrape_linkedin_jobs

__all__ = [
    "ScrapedJob",
    "compute_job_hash",
    "upsert_scraped_jobs",
    "parse_linkedin_html",
    "scrape_linkedin_jobs",
    "parse_gupy_html",
    "scrape_gupy_jobs",
]
