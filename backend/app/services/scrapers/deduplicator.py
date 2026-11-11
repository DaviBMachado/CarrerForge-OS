import hashlib
import re

from sqlmodel import Session, select

from backend.app.models import Experience, JobPosting, MasterProfile
from backend.app.services.scrapers.base import ScrapedJob
from backend.app.services.tailoring_engine import extract_ats_keywords


def _normalize_text(value: str) -> str:
    lowered = value.strip().lower()
    cleaned = re.sub(r"[^\w\s]", "", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def compute_job_hash(
    platform: str,
    external_id: str | None = None,
    company: str = "",
    title: str = "",
) -> str:
    norm_platform = _normalize_text(platform or "generic")
    if external_id and external_id.strip():
        raw_key = f"{norm_platform}:{external_id.strip()}"
    else:
        norm_company = _normalize_text(company)
        norm_title = _normalize_text(title)
        raw_key = f"{norm_company}:{norm_title}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def upsert_scraped_jobs(
    session: Session,
    user_id: int,
    scraped_jobs: list[ScrapedJob],
) -> list[JobPosting]:
    existing_jobs = session.exec(select(JobPosting).where(JobPosting.user_id == user_id)).all()
    profile = session.exec(select(MasterProfile).where(MasterProfile.user_id == user_id)).first()
    experiences = (
        list(session.exec(select(Experience).where(Experience.profile_id == profile.id)).all())
        if profile and profile.id
        else []
    )

    def _score(desc: str | None) -> int | None:
        if not profile or not desc:
            return None
        job_kws, _, match_pct = extract_ats_keywords(desc, profile, experiences)
        return match_pct if job_kws else 0

    by_hash: dict[str, JobPosting] = {}
    for job in existing_jobs:
        ext_hash = compute_job_hash(job.platform, job.external_id, job.company, job.title)
        title_hash = compute_job_hash(job.platform, None, job.company, job.title)
        by_hash[ext_hash] = job
        by_hash[title_hash] = job

    results: list[JobPosting] = []
    for item in scraped_jobs:
        ext_hash = compute_job_hash(item.platform, item.external_id, item.company, item.title)
        title_hash = compute_job_hash(item.platform, None, item.company, item.title)

        existing = by_hash.get(ext_hash) or by_hash.get(title_hash)
        combined_desc = item.description
        if item.requirements and item.requirements not in combined_desc:
            combined_desc = f"{combined_desc}\n\n{item.requirements}".strip()

        if existing is not None:
            if item.external_id and not existing.external_id:
                existing.external_id = item.external_id
            if item.location:
                existing.location = item.location
            if item.remote_type:
                existing.remote_type = item.remote_type
            if item.url:
                existing.url = item.url
            if combined_desc:
                existing.raw_description = combined_desc
                existing.cleaned_text = combined_desc
            score = _score(combined_desc or existing.cleaned_text or existing.raw_description)
            if score is not None:
                existing.match_percentage = score
            session.add(existing)
            results.append(existing)
        else:
            new_job = JobPosting(
                user_id=user_id,
                platform=item.platform,
                external_id=item.external_id or ext_hash[:16],
                title=item.title,
                company=item.company,
                location=item.location,
                remote_type=item.remote_type or item.employment_type,
                url=item.url,
                raw_description=combined_desc,
                cleaned_text=combined_desc,
                language=item.language,
                status="discovered",
                match_percentage=_score(combined_desc),
            )
            session.add(new_job)
            by_hash[ext_hash] = new_job
            by_hash[title_hash] = new_job
            results.append(new_job)

    session.commit()
    for job in results:
        session.refresh(job)
    return results
