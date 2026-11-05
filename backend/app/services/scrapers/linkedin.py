import asyncio
import html
import random
import re
from collections.abc import Callable
from urllib.parse import quote_plus

from backend.app.services.browser_manager import get_authenticated_context
from backend.app.services.scrapers.base import ScrapedJob

MIN_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 5.0
DEFAULT_BATCH_CAP = 25
MAX_BATCH_CAP = 50


def _strip_tags(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return _strip_tags(match.group(1))


def _clean_linkedin_url(url: str | None) -> str | None:
    if not url:
        return None
    base = url.split("?")[0].strip()
    return base


def parse_linkedin_html(raw_html: str) -> list[ScrapedJob]:
    # shortcut: regex parser on standard card markup, upgrade to DOM/BS4 if LinkedIn nesting shifts
    blocks = re.findall(
        r"<li[^>]*data-occludable-job-id=[^>]*>.*?</li>|<div[^>]*base-search-card[^>]*>.*?</div>\s*</li>",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not blocks:
        blocks = re.findall(
            r"<div[^>]*class=[\"'][^\"']*base-search-card[^\"']*[\"'][^>]*>.*?</div>",
            raw_html,
            flags=re.IGNORECASE | re.DOTALL,
        )

    jobs: list[ScrapedJob] = []
    for block in blocks:
        ext_id = _extract_first(r'data-occludable-job-id=["\']([^"\']+)["\']', block)
        if not ext_id:
            ext_id = _extract_first(r"urn:li:jobPosting:([0-9A-Za-z_-]+)", block)

        url_match = re.search(
            r'<a[^>]*class=["\'][^"\']*base-card__full-link[^"\']*["\'][^>]*href=["\']([^"\']+)["\']',
            block,
            flags=re.IGNORECASE,
        )
        raw_url = url_match.group(1) if url_match else None
        clean_url = _clean_linkedin_url(raw_url)

        if not ext_id and clean_url:
            ext_id = _extract_first(r"/jobs/view/([^/?#]+)", clean_url)

        title = _extract_first(
            r'<h3[^>]*class=["\'][^"\']*base-search-card__title[^"\']*["\'][^>]*>(.*?)</h3>',
            block,
        )
        company = _extract_first(
            r'<h4[^>]*class=["\'][^"\']*base-search-card__subtitle[^"\']*["\'][^>]*>(.*?)</h4>',
            block,
        )
        location = _extract_first(
            r'<span[^>]*class=["\'][^"\']*job-search-card__location[^"\']*["\'][^>]*>(.*?)</span>',
            block,
        )
        emp_type = _extract_first(
            r'<span[^>]*class=["\'][^"\']*job-search-card__employment-type[^"\']*["\'][^>]*>(.*?)</span>',
            block,
        )
        description = (
            _extract_first(
                r'<div[^>]*class=["\'][^"\']*(?:show-more-less-html__markup|jobs-description__content)[^"\']*["\'][^>]*>(.*?)</div>',
                block,
            )
            or ""
        )

        if not title or not company:
            continue

        remote_type = "remote" if location and "remote" in location.lower() else emp_type
        jobs.append(
            ScrapedJob(
                platform="linkedin",
                external_id=ext_id,
                title=title,
                company=company,
                location=location,
                employment_type=emp_type,
                remote_type=remote_type,
                description=description,
                url=clean_url,
                language="en",
            )
        )
    return jobs


async def human_jitter() -> float:
    delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
    await asyncio.sleep(delay)
    return delay


async def scrape_linkedin_jobs(
    query: str,
    location: str = "",
    remote: bool = False,
    max_jobs: int = DEFAULT_BATCH_CAP,
    on_log: Callable[[str], None] | None = None,
) -> list[ScrapedJob]:
    capped_limit = max(1, min(max_jobs, MAX_BATCH_CAP))
    params = f"keywords={quote_plus(query)}"
    if location:
        params += f"&location={quote_plus(location)}"
    if remote:
        params += "&f_WT=2"
    search_url = f"https://www.linkedin.com/jobs/search/?{params}"

    if on_log:
        on_log(f"Navigating to LinkedIn search: {search_url}")

    async with get_authenticated_context(headless=True) as context:
        page = await context.new_page()
        await page.goto(search_url, wait_until="domcontentloaded")
        await human_jitter()
        content = await page.content()

    parsed = parse_linkedin_html(content)
    results = parsed[:capped_limit]
    if on_log:
        on_log(f"Scraped {len(results)} LinkedIn jobs.")
    return results
