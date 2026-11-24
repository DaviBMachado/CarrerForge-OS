import re
from urllib.parse import urlparse

from backend.app.services.browser_manager import get_authenticated_context
from backend.app.services.scrapers.base import ScrapedJob


def is_job_url(query_or_url: str) -> bool:
    cleaned = query_or_url.strip()
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        return False
    parsed = urlparse(cleaned)
    return bool(parsed.scheme and parsed.netloc)


async def extract_linkedin_job(page) -> ScrapedJob:
    curr_url = page.url.lower()
    if "authwall" in curr_url or "checkpoint" in curr_url or "login" in curr_url:
        raise RuntimeError(
            "LinkedIn redirecionou para tela de login/authwall. "
            "Autentique a sessão no Job Radar ou cadastre a vaga manualmente."
        )

    # shortcut: wait for standard LinkedIn job view containers, upgrade if LinkedIn changes dynamic IDs
    for sel in (
        "h1.top-card-layout__title",
        "h1.job-details-jobs-unified-top-card__job-title",
        ".jobs-unified-top-card__job-title",
        "h1",
    ):
        try:
            await page.wait_for_selector(sel, timeout=5000)
            break
        except Exception:
            continue

    data = await page.evaluate(
        """() => {
            const getTxt = (selectors) => {
                for (const s of selectors) {
                    const el = document.querySelector(s);
                    if (el && el.innerText && el.innerText.trim()) {
                        return el.innerText.trim();
                    }
                }
                return '';
            };
            const title = getTxt([
                'h1.top-card-layout__title',
                'h1.job-details-jobs-unified-top-card__job-title',
                '.jobs-unified-top-card__job-title',
                'h1'
            ]);
            const company = getTxt([
                'a.topcard__org-name-link',
                'span.job-details-jobs-unified-top-card__company-name',
                '.jobs-unified-top-card__company-name a',
                '.jobs-unified-top-card__company-name',
                'a[data-tracking-control-name="public_jobs_topcard-org-name"]',
                '.top-card-layout__first-subline a'
            ]);
            const location = getTxt([
                'span.topcard__flavor--bullet',
                'span.job-details-jobs-unified-top-card__bullet',
                '.jobs-unified-top-card__bullet',
                '.top-card-layout__second-subline',
                '.topcard__flavor-row span'
            ]);
            const descEl = document.querySelector(
                '.show-more-less-html__markup, .jobs-description__content, #job-details, article.jobs-description__container, .description__text'
            );
            const description = descEl ? descEl.innerText.trim() : '';
            return { title, company, location, description };
        }"""
    )

    title = data.get("title")
    company = data.get("company")
    desc = data.get("description")
    loc = data.get("location")

    if not title or not company:
        raise ValueError("Não foi possível identificar o título ou empresa na página do LinkedIn.")

    clean_url = page.url.split("?")[0]
    ext_id_match = re.search(r"/jobs/view/([^/?#]+)", clean_url)
    ext_id = ext_id_match.group(1) if ext_id_match else None
    remote_type = "remote" if loc and "remote" in loc.lower() else None

    return ScrapedJob(
        platform="linkedin",
        external_id=ext_id,
        title=title,
        company=company,
        location=loc or None,
        remote_type=remote_type,
        description=desc or "",
        url=clean_url,
        language="en",
    )


async def extract_gupy_job(page) -> ScrapedJob:
    for sel in ('[data-testid="job-title"]', "h1.job-title", "h1"):
        try:
            await page.wait_for_selector(sel, timeout=5000)
            break
        except Exception:
            continue

    data = await page.evaluate(
        """() => {
            const getTxt = (selectors) => {
                for (const s of selectors) {
                    const el = document.querySelector(s);
                    if (el && el.innerText && el.innerText.trim()) {
                        return el.innerText.trim();
                    }
                }
                return '';
            };
            const title = getTxt([
                '[data-testid="job-title"]',
                'h1[data-testid="job-title"]',
                'h1.job-title',
                'h1'
            ]);
            const company = getTxt([
                '[data-testid="company-name"]',
                '.company-name',
                'p[data-testid="job-company-name"]'
            ]);
            const location = getTxt([
                '[data-testid="job-location"]',
                '.job-location',
                'span[data-testid="job-location"]'
            ]);
            const descEl = document.querySelector(
                '[data-testid="text-section"], .job-description, section[data-testid="job-description-section"], main'
            );
            const description = descEl ? descEl.innerText.trim() : '';
            return { title, company, location, description };
        }"""
    )

    title = data.get("title")
    company = data.get("company")
    desc = data.get("description")
    loc = data.get("location")

    if not company:
        parsed = urlparse(page.url)
        subdomain = parsed.netloc.split(".")[0]
        if subdomain and subdomain != "gupy":
            company = subdomain.capitalize()

    if not title or not company:
        raise ValueError("Não foi possível identificar o título ou empresa na página da Gupy.")

    clean_url = page.url.split("?")[0]
    ext_id_match = re.search(r"/jobs/([^/?#]+)", clean_url)
    ext_id = ext_id_match.group(1) if ext_id_match else None
    remote_type = "remote" if loc and ("remoto" in loc.lower() or "remote" in loc.lower()) else None

    return ScrapedJob(
        platform="gupy",
        external_id=ext_id,
        title=title,
        company=company,
        location=loc or None,
        remote_type=remote_type,
        description=desc or "",
        url=clean_url,
        language="pt",
    )


async def extract_generic_job(page) -> ScrapedJob:
    data = await page.evaluate(
        """() => {
            const getMeta = (names) => {
                for (const n of names) {
                    const el = document.querySelector(`meta[property="${n}"], meta[name="${n}"]`);
                    if (el && el.getAttribute('content')) {
                        return el.getAttribute('content').trim();
                    }
                }
                return '';
            };
            const ogTitle = getMeta(['og:title', 'twitter:title']);
            const ogSite = getMeta(['og:site_name', 'application-name']);
            const ogDesc = getMeta(['og:description', 'twitter:description', 'description']);

            const h1 = document.querySelector('h1')?.innerText?.trim() || '';
            const docTitle = document.title ? document.title.trim() : '';
            const title = ogTitle || h1 || docTitle;

            const mainEl = document.querySelector('main, article, [role="main"], body');
            const bodyText = mainEl ? mainEl.innerText.trim() : '';

            return {
                title,
                siteName: ogSite,
                ogDesc,
                bodyText: bodyText.slice(0, 5000)
            };
        }"""
    )
    title = data.get("title") or "Vaga Sem Título"
    company = data.get("siteName")
    if not company:
        parsed = urlparse(page.url)
        company = parsed.netloc.replace("www.", "").split(".")[0].capitalize()

    desc = data.get("ogDesc") or data.get("bodyText") or ""
    if not desc:
        raise ValueError("Não foi possível extrair a descrição do conteúdo da página.")

    clean_url = page.url.split("?")[0]
    return ScrapedJob(
        platform="generic",
        external_id=None,
        title=title[:120],
        company=company[:80],
        location=None,
        remote_type=None,
        description=desc,
        url=clean_url,
        language="en",
    )


async def scrape_job_by_url(url: str, headless: bool = True) -> ScrapedJob:
    if not is_job_url(url):
        raise ValueError("URL inválida. Forneça uma URL iniciando com http:// ou https://.")

    domain = urlparse(url).netloc.lower()

    async with get_authenticated_context(headless=headless) as context:
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        except Exception as exc:
            raise RuntimeError(f"Erro ao acessar a página: {exc}")

        if "linkedin.com" in domain:
            return await extract_linkedin_job(page)
        elif "gupy.io" in domain:
            return await extract_gupy_job(page)
        else:
            return await extract_generic_job(page)
