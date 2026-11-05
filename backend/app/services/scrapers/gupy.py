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


def parse_gupy_html(raw_html: str) -> list[ScrapedJob]:
    # shortcut: regex parser on Gupy card markup, upgrade to DOM parser if portal changes
    blocks = re.findall(
        r"<article[^>]*class=[\"'][^\"']*job-card[^\"']*[\"'][^>]*>.*?</article>",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not blocks:
        blocks = re.findall(
            r"<li[^>]*data-testid=[\"']job-list__item[\"'][^>]*>.*?</li>",
            raw_html,
            flags=re.IGNORECASE | re.DOTALL,
        )

    jobs: list[ScrapedJob] = []
    for block in blocks:
        ext_id = _extract_first(r'data-job-id=["\']([^"\']+)["\']', block)
        url_match = re.search(r'<a[^>]*href=["\']([^"\']+)["\']', block, flags=re.IGNORECASE)
        url = url_match.group(1).strip() if url_match else None

        if not ext_id and url:
            ext_id = _extract_first(r"/jobs/([0-9A-Za-z_-]+)", url)

        title = _extract_first(
            r'<h[23][^>]*class=["\'][^"\']*job-title[^"\']*["\'][^>]*>(.*?)</h[23]>',
            block,
        ) or _extract_first(r"<h[23][^>]*>(.*?)</h[23]>", block)

        company = _extract_first(
            r'<span[^>]*class=["\'][^"\']*company-name[^"\']*["\'][^>]*>(.*?)</span>',
            block,
        ) or _extract_first(
            r'<p[^>]*class=["\'][^"\']*company[^"\']*["\'][^>]*>(.*?)</p>',
            block,
        )

        location = _extract_first(
            r'<span[^>]*class=["\'][^"\']*job-location[^"\']*["\'][^>]*>(.*?)</span>',
            block,
        )
        description = (
            _extract_first(
                r'<div[^>]*class=["\'][^"\']*job-description[^"\']*["\'][^>]*>(.*?)</div>',
                block,
            )
            or ""
        )
        requirements = _extract_first(
            r'<div[^>]*class=["\'][^"\']*job-requirements[^"\']*["\'][^>]*>(.*?)</div>',
            block,
        )

        if not title or not company:
            continue

        jobs.append(
            ScrapedJob(
                platform="gupy",
                external_id=ext_id,
                title=title,
                company=company,
                location=location,
                requirements=requirements,
                description=description,
                url=url,
                language="pt",
            )
        )
    return jobs


async def human_jitter() -> float:
    delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
    await asyncio.sleep(delay)
    return delay


async def scrape_gupy_jobs(
    query: str,
    location: str = "",
    max_jobs: int = DEFAULT_BATCH_CAP,
    on_log: Callable[[str], None] | None = None,
) -> list[ScrapedJob]:
    capped_limit = max(1, min(max_jobs, MAX_BATCH_CAP))
    search_term = f"{query} {location}".strip()
    search_url = f"https://portal.gupy.io/job-search/term={quote_plus(search_term)}"

    if on_log:
        on_log(f"Navigating to Gupy search: {search_url}")

    async with get_authenticated_context(headless=True) as context:
        page = await context.new_page()
        await page.goto(search_url, wait_until="domcontentloaded")
        await human_jitter()
        content = await page.content()

    parsed = parse_gupy_html(content)
    results = parsed[:capped_limit]
    if on_log:
        on_log(f"Scraped {len(results)} Gupy jobs.")
    return results
