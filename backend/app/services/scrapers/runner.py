import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session

from backend.app.db import engine
from backend.app.services.scrapers.base import ScrapedJob
from backend.app.services.scrapers.deduplicator import upsert_scraped_jobs
from backend.app.services.scrapers.gupy import DEFAULT_BATCH_CAP, scrape_gupy_jobs
from backend.app.services.scrapers.linkedin import scrape_linkedin_jobs


@dataclass
class ScraperRun:
    run_id: str
    status: str = "running"
    progress: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[Any] | None = None
    done: bool = False


# shortcut: in-memory run registry for single-process runner, upgrade to Redis if multi-worker
MAX_STORED_RUNS: int = 10
RUNS: dict[str, ScraperRun] = {}


def evict_old_runs(target_cap: int = MAX_STORED_RUNS) -> None:
    """Evict oldest finished runs in FIFO order so RUNS does not exceed target_cap."""
    excess = len(RUNS) - target_cap
    if excess <= 0:
        return
    done_keys = [k for k, r in RUNS.items() if r.done]
    for k in done_keys[:excess]:
        RUNS.pop(k, None)


async def _emit(
    run: ScraperRun,
    message: str,
    progress: int | None = None,
    status: str | None = None,
    **extra: Any,
) -> None:
    if progress is not None:
        run.progress = progress
    if status is not None:
        run.status = status
    event: dict[str, Any] = {
        "run_id": run.run_id,
        "message": message,
        "progress": run.progress,
        "status": run.status,
        **extra,
    }
    run.events.append(event)
    async with run.condition:
        run.condition.notify_all()


def _emit_sync(
    run: ScraperRun,
    message: str,
    progress: int | None = None,
    status: str | None = None,
) -> None:
    if progress is not None:
        run.progress = progress
    if status is not None:
        run.status = status
    run.events.append(
        {
            "run_id": run.run_id,
            "message": message,
            "progress": run.progress,
            "status": run.status,
        }
    )
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_notify_condition(run))
    except RuntimeError:
        pass


async def _notify_condition(run: ScraperRun) -> None:
    async with run.condition:
        run.condition.notify_all()


async def _execute_run(
    run: ScraperRun,
    query: str,
    location: str,
    platforms: list[str],
    remote: bool,
    max_jobs: int,
    user_id: int = 1,
) -> None:
    try:
        await _emit(run, f"Starting scraper for '{query}'...", progress=5, status="running")
        all_scraped: list[ScrapedJob] = []
        active_platforms = [p.lower() for p in platforms if p.lower() in ("linkedin", "gupy")]
        if not active_platforms:
            active_platforms = ["linkedin", "gupy"]

        step_span = 75 // len(active_platforms)

        for idx, platform in enumerate(active_platforms):
            base_progress = 10 + idx * step_span
            await _emit(
                run,
                f"Navigating to {platform.capitalize()} search...",
                progress=base_progress,
            )

            def on_log(msg: str, bp: int = base_progress, sp: int = step_span) -> None:
                _emit_sync(run, msg, progress=min(85, bp + sp // 2))

            if platform == "linkedin":
                jobs = await scrape_linkedin_jobs(
                    query=query,
                    location=location,
                    remote=remote,
                    max_jobs=max_jobs,
                    on_log=on_log,
                )
            else:
                jobs = await scrape_gupy_jobs(
                    query=query,
                    location=location,
                    remote=remote,
                    max_jobs=max_jobs,
                    on_log=on_log,
                )
            all_scraped.extend(jobs)

        await _emit(
            run,
            f"Scraped {len(all_scraped)} jobs. Saving to database...",
            progress=90,
        )

        saved_count = 0
        if all_scraped:
            with Session(engine) as session:
                saved = upsert_scraped_jobs(session, user_id, all_scraped)
                saved_count = len(saved)

        await _emit(
            run,
            "Done.",
            progress=100,
            status="completed",
            jobs_scraped=len(all_scraped),
            jobs_saved=saved_count,
        )
    except asyncio.CancelledError:
        await _emit(run, "Scraper aborted.", status="stopped")
        raise
    except Exception as exc:
        await _emit(run, f"Error: {exc}", status="failed")
    finally:
        run.done = True
        evict_old_runs(MAX_STORED_RUNS)
        async with run.condition:
            run.condition.notify_all()


async def start_scraper_run(
    query: str,
    location: str = "",
    platforms: list[str] | None = None,
    remote: bool = False,
    max_jobs: int = DEFAULT_BATCH_CAP,
    user_id: int = 1,
) -> ScraperRun:
    evict_old_runs(MAX_STORED_RUNS - 1)
    run_id = uuid.uuid4().hex
    run = ScraperRun(run_id=run_id)
    RUNS[run_id] = run
    run.task = asyncio.create_task(
        _execute_run(
            run=run,
            query=query,
            location=location,
            platforms=platforms or ["linkedin", "gupy"],
            remote=remote,
            max_jobs=max_jobs,
            user_id=user_id,
        )
    )
    return run


async def stop_scraper_run(run_id: str | None = None) -> ScraperRun | None:
    target: ScraperRun | None = None
    if run_id:
        target = RUNS.get(run_id)
    else:
        for r in reversed(list(RUNS.values())):
            if not r.done:
                target = r
                break
        if target is None and RUNS:
            target = list(RUNS.values())[-1]

    if target is None:
        return None

    if target.task and not target.done:
        target.task.cancel()
        try:
            await target.task
        except asyncio.CancelledError:
            pass
    elif not target.done:
        target.done = True
        await _emit(target, "Scraper aborted.", status="stopped")
        evict_old_runs(MAX_STORED_RUNS)

    return target


async def stream_run_events(run_id: str) -> AsyncGenerator[str, None]:
    run = RUNS.get(run_id)
    if not run:
        return

    idx = 0
    while True:
        while idx < len(run.events):
            payload = json.dumps(run.events[idx])
            yield f"data: {payload}\n\n"
            idx += 1
        if run.done and idx >= len(run.events):
            break
        async with run.condition:
            if idx >= len(run.events) and not run.done:
                await run.condition.wait()
