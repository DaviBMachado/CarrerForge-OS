import asyncio
import html
import random
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote_plus, urlencode

from backend.app.services.browser_manager import DEFAULT_USER_AGENT, get_authenticated_context
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


def parse_gupy_api_item(item: dict[str, Any]) -> ScrapedJob | None:
    job_id = str(item.get("id") or "")
    title = str(item.get("name") or "").strip()
    company = str(item.get("careerPageName") or "").strip()
    if not title or not company:
        return None

    city = str(item.get("city") or "").strip()
    state = str(item.get("state") or "").strip()
    loc_parts = [p for p in (city, state) if p]
    fallback_loc = str(item.get("workplaceType") or "").strip() or None
    location = " - ".join(loc_parts) if loc_parts else fallback_loc

    url = item.get("jobUrl") or item.get("careerPageUrl")
    description = item.get("description") or ""

    return ScrapedJob(
        platform="gupy",
        external_id=job_id or None,
        title=title,
        company=company,
        location=location,
        requirements=None,
        description=description,
        url=str(url) if url else None,
        language="pt",
    )


async def human_jitter() -> float:
    delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
    await asyncio.sleep(delay)
    return delay


async def scrape_gupy_jobs(
    query: str,
    location: str = "",
    remote: bool = False,
    max_jobs: int = DEFAULT_BATCH_CAP,
    on_log: Callable[[str], None] | None = None,
) -> list[ScrapedJob]:
    capped_limit = max(1, min(max_jobs, MAX_BATCH_CAP))
    clean_query = query.strip()
    clean_loc = location.strip()
    is_remote = remote or clean_loc.lower() in ("remote", "remoto")
    if clean_loc.lower() in ("remote", "remoto"):
        clean_loc = ""

    params: dict[str, Any] = {
        "jobName": clean_query,
        "limit": capped_limit,
        "offset": 0,
    }
    if is_remote:
        params["workplaceType"] = "remote"
    if clean_loc:
        params["city"] = clean_loc

    search_url = f"https://portal.gupy.io/job-search/term={quote_plus(clean_query)}"
    if clean_loc:
        search_url += f"&city={quote_plus(clean_loc)}"
    if is_remote:
        search_url += "&workplaceType=remote"

    if on_log:
        on_log(
            f"Searching Gupy: query='{clean_query}', "
            f"location='{clean_loc or ('remote' if is_remote else 'any')}'"
        )

    jobs: list[ScrapedJob] = []

    async with get_authenticated_context(headless=True) as context:
        # 1. Primary path: Use Gupy's search API via authenticated browser context
        if hasattr(context, "request") and hasattr(context.request, "get"):

            async def _fetch(p: dict[str, Any]) -> list[ScrapedJob]:
                url = f"https://portal.gupy.io/api/job-search/jobs?{urlencode(p)}"
                try:
                    res = await context.request.get(
                        url,
                        headers={
                            "User-Agent": DEFAULT_USER_AGENT,
                            "Accept": "application/json",
                        },
                    )
                    if res.status == 200:
                        payload = await res.json()
                        raw_data = payload.get("data", []) if isinstance(payload, dict) else []
                        items: list[ScrapedJob] = []
                        for item in raw_data:
                            parsed_job = parse_gupy_api_item(item)
                            if parsed_job:
                                items.append(parsed_job)
                        return items
                except Exception:
                    pass
                return []

            # 1a. Direct search with requested city/location
            jobs = await _fetch(params)

            # 1b. Fallback: if city returned 0 results, try state or broaden without strict city
            if not jobs and clean_loc:
                if on_log:
                    on_log(f"Broadening Gupy search beyond city '{clean_loc}'...")
                state_params = {**params, "state": clean_loc}
                state_params.pop("city", None)
                jobs = await _fetch(state_params)
                if not jobs:
                    fallback_params = {**params}
                    fallback_params.pop("city", None)
                    jobs = await _fetch(fallback_params)

            # 1c. If compound query still returned 0, try the first keyword as fallback
            if not jobs and " " in clean_query:
                primary_kw = clean_query.split()[0]
                if len(primary_kw) >= 3:
                    if on_log:
                        on_log(f"Broadening Gupy search from '{clean_query}' to '{primary_kw}'...")
                    kw_params = {**params, "jobName": primary_kw}
                    kw_params.pop("city", None)
                    jobs = await _fetch(kw_params)

        # 2. Fallback path: HTML parsing (e.g. for mocked unit tests or SSR)
        if not jobs and hasattr(context, "new_page"):
            page = await context.new_page()
            await page.goto(search_url, wait_until="domcontentloaded")
            await human_jitter()
            content = await page.content()
            jobs = parse_gupy_html(content)

    results = jobs[:capped_limit]
    if on_log:
        on_log(f"Scraped {len(results)} Gupy jobs.")
    return results
