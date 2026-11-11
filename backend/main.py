import time
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from backend.app.db import init_db
from backend.app.logging_config import get_logger, get_runtime_logs, setup_logging
from backend.app.routers.compiler import router as compiler_router
from backend.app.routers.profile import router as profile_router
from backend.app.routers.scraper import router as scraper_router
from backend.app.routers.settings import router as settings_router
from backend.app.routers.tailor import router as tailor_router

setup_logging()
logger = get_logger("careerforge.http")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        yield
    finally:
        from backend.app.services.browser_manager import close_interactive_session

        await close_interactive_session()


app = FastAPI(title="CareerForge OS", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def http_request_tracing_middleware(request: Request, call_next):
    start = time.perf_counter()
    method = request.method
    path = request.url.path
    query = f"?{request.url.query}" if request.url.query else ""
    full_path = f"{path}{query}"

    try:
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        status = response.status_code
        try:
            status_phrase = HTTPStatus(status).phrase
            status_desc = f"{status} {status_phrase}"
        except ValueError:
            status_desc = str(status)

        log_msg = f"[{method}] {full_path} -> {status_desc} ({elapsed_ms:.1f}ms)"
        if status >= 500:
            logger.error(log_msg)
        elif status >= 400:
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        return response
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.error(
            f"[{method}] {full_path} -> 500 Internal Server Error ({elapsed_ms:.1f}ms): {exc}",
            exc_info=True,
        )
        raise


app.include_router(compiler_router, prefix="/api")
app.include_router(profile_router, prefix="/api")
app.include_router(scraper_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(tailor_router, prefix="/api")

STATIC_DIR = Path(__file__).resolve().parent / "app" / "static"
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _get_static_dir() -> Path:
    if (STATIC_DIR / "index.html").is_file():
        return STATIC_DIR
    if (FRONTEND_DIST / "index.html").is_file():
        return FRONTEND_DIST
    return STATIC_DIR


@app.get("/api/health")
def get_health() -> dict[str, str]:
    return {
        "status": "healthy",
        "app": "CareerForge OS",
        "version": "0.1.0",
    }


@app.get("/api/system/logs")
def get_system_logs(limit: int = 150) -> dict[str, list[dict[str, str]]]:
    return {"logs": get_runtime_logs(limit=limit)}


@app.get("/{full_path:path}")
def serve_spa(full_path: str):
    if full_path == "api" or full_path.startswith("api/"):
        route_suffix = full_path[4:] if full_path.startswith("api/") else ""
        raise HTTPException(
            status_code=404,
            detail=(
                f"API route '/api/{route_suffix}' not found. "
                "Verify the endpoint path and HTTP method."
            ),
        )

    static_dir = _get_static_dir()
    target_file = (static_dir / full_path).resolve()
    # Prevent directory traversal attacks
    if (
        full_path
        and target_file.is_file()
        and str(target_file).startswith(str(static_dir.resolve()))
    ):
        return FileResponse(target_file)

    index_file = static_dir / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)

    if not full_path or full_path == "":
        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "app": "CareerForge OS",
                "message": "Backend running. Frontend build not found at backend/app/static.",
            },
        )

    raise HTTPException(status_code=404, detail="Not found")
