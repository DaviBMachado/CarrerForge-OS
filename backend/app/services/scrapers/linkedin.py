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
    # shortcut: regex parser on standard and authenticated card markup, upgrade to DOM/BS4 if LinkedIn nesting shifts
    blocks = re.findall(
        r"<li[^>]*data-occludable-job-id=[^>]*>.*?</li>|<div[^>]*base-search-card[^>]*>.*?</div>\s*</li>|<li[^>]*class=[\"'][^\"']*jobs-search-results__list-item[^\"']*[\"'][^>]*>.*?</li>|<div[^>]*class=[\"'][^\"']*job-card-container[^\"']*[\"'][^>]*>.*?</div>",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not blocks:
        blocks = re.findall(
            r"<div[^>]*class=[\"'][^\"']*(?:base-search-card|job-card-container)[^\"']*[\"'][^>]*>.*?</div>",
            raw_html,
            flags=re.IGNORECASE | re.DOTALL,
        )

    jobs: list[ScrapedJob] = []
    for block in blocks:
        ext_id = _extract_first(r'data-occludable-job-id=["\']([^"\']+)["\']', block)
        if not ext_id:
            ext_id = _extract_first(r'data-job-id=["\']([^"\']+)["\']', block)
        if not ext_id:
            ext_id = _extract_first(r"urn:li:jobPosting:([0-9A-Za-z_-]+)", block)

        url_match = re.search(
            r'<a[^>]*href=["\']([^"\']+)["\'][^>]*class=["\'][^"\']*(?:base-card__full-link|job-card-container__link|job-card-list__title)[^"\']*',
            block,
            flags=re.IGNORECASE,
        ) or re.search(
            r'<a[^>]*class=["\'][^"\']*(?:base-card__full-link|job-card-container__link|job-card-list__title)[^"\']*["\'][^>]*href=["\']([^"\']+)["\']',
            block,
            flags=re.IGNORECASE,
        ) or re.search(
            r'<a[^>]*href=["\'](https?://[^"\']*linkedin\.com/jobs/view/[^"\']+)["\']',
            block,
            flags=re.IGNORECASE,
        )
        raw_url = url_match.group(1) if url_match else None
        clean_url = _clean_linkedin_url(raw_url)

        if not ext_id and clean_url:
            ext_id = _extract_first(r"/jobs/view/([^/?#]+)", clean_url)

        title = _extract_first(
            r'<(?:h3|a|strong)[^>]*class=["\'][^"\']*(?:base-search-card__title|job-card-list__title|job-card-container__link)[^"\']*["\'][^>]*>(.*?)</(?:h3|a|strong)>',
            block,
        ) or _extract_first(
            r'<h3[^>]*>(.*?)</h3>',
            block,
        )
        company = _extract_first(
            r'<(?:h4|span|div|a)[^>]*class=["\'][^"\']*(?:base-search-card__subtitle|job-card-container__primary-description|job-card-container__company-name)[^"\']*["\'][^>]*>(.*?)</(?:h4|span|div|a)>',
            block,
        ) or _extract_first(
            r'<h4[^>]*>(.*?)</h4>',
            block,
        )
        location = _extract_first(
            r'<(?:span|li)[^>]*class=["\'][^"\']*(?:job-search-card__location|job-card-container__metadata-item)[^"\']*["\'][^>]*>(.*?)</(?:span|li)>',
            block,
        )
        emp_type = _extract_first(
            r'<span[^>]*class=["\'][^"\']*(?:job-search-card__employment-type|job-card-container__metadata-item)[^"\']*["\'][^>]*>(.*?)</span>',
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

    dom_extracted: list[dict[str, str]] = []
    content = ""

    async with get_authenticated_context(headless=True) as context:
        page = await context.new_page()
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await human_jitter()

            curr_url = page.url.lower()
            if "authwall" in curr_url or "checkpoint" in curr_url or "login" in curr_url:
                if on_log:
                    on_log(
                        f"LinkedIn redirected to {page.url}. Authwall/checkpoint detected. Please verify or refresh login session in Job Radar."
                    )

            # Attempt waiting for either authenticated or public cards to populate
            for selector in (
                "div.job-card-container",
                "li.jobs-search-results__list-item",
                "div.base-search-card",
                "li[data-occludable-job-id]",
                "div.job-search-card",
            ):
                try:
                    await page.wait_for_selector(selector, timeout=4000)
                    break
                except Exception:
                    continue

            # Gently scroll down to trigger lazy loading of search results
            try:
                await page.evaluate("window.scrollBy(0, 800);")
                await asyncio.sleep(1.0)
            except Exception:
                pass

            # Extract via live DOM query with support for both authenticated and guest DOMs
            dom_extracted = await page.evaluate(
                """() => {
                    const results = [];
                    const selectors = [
                        'li.jobs-search-results__list-item',
                        'div.job-card-container',
                        'div.base-search-card',
                        'li[data-occludable-job-id]',
                        'div.job-search-card'
                    ];
                    const nodes = document.querySelectorAll(selectors.join(', '));
                    for (const node of nodes) {
                        const titleEl = node.querySelector('.job-card-list__title, .base-search-card__title, a[data-control-name="job_card_click"], a.job-card-container__link, h3, strong');
                        const companyEl = node.querySelector('.job-card-container__primary-description, .base-search-card__subtitle, .job-card-container__company-name, h4, span.job-card-container__primary-description');
                        const locEl = node.querySelector('.job-card-container__metadata-item, .job-search-card__location, span.job-card-container__metadata-wrapper, li.job-card-container__metadata-item');
                        const linkEl = node.querySelector('a.job-card-container__link, a.base-card__full-link, a[href*="/jobs/view/"], a');
                        const title = titleEl ? titleEl.innerText.trim() : '';
                        const company = companyEl ? companyEl.innerText.trim() : '';
                        const loc = locEl ? locEl.innerText.trim() : '';
                        const href = linkEl && linkEl.href ? linkEl.href : '';
                        const extId = node.getAttribute('data-occludable-job-id') || node.getAttribute('data-job-id') || '';
                        if (title && company) {
                            results.push({ title, company, location: loc, url: href, external_id: extId });
                        }
                    }
                    return results;
                }"""
            )
            content = await page.content()
        except Exception as exc:
            if on_log:
                on_log(f"LinkedIn page navigation encounter: {exc}")
            try:
                content = await page.content() if page else ""
            except Exception:
                content = ""

    results: list[ScrapedJob] = []
    if dom_extracted:
        for item in dom_extracted:
            raw_url = item.get("url")
            clean_url = _clean_linkedin_url(raw_url)
            ext_id = item.get("external_id")
            if not ext_id and clean_url:
                ext_id = _extract_first(r"/jobs/view/([^/?#]+)", clean_url)
            loc = item.get("location") or ""
            remote_type = "remote" if "remote" in loc.lower() else None
            results.append(
                ScrapedJob(
                    platform="linkedin",
                    external_id=ext_id,
                    title=item["title"],
                    company=item["company"],
                    location=loc or None,
                    remote_type=remote_type,
                    description="",
                    url=clean_url,
                    language="en",
                )
            )

    if not results and content:
        results = parse_linkedin_html(content)

    final_results = results[:capped_limit]
    if on_log:
        on_log(f"Scraped {len(final_results)} LinkedIn jobs.")
    return final_results
