from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from backend.app.db import init_db
from backend.app.routers.compiler import router as compiler_router
from backend.app.routers.profile import router as profile_router
from backend.app.routers.scraper import router as scraper_router
from backend.app.routers.settings import router as settings_router
from backend.app.routers.tailor import router as tailor_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="CareerForge OS", version="0.1.0", lifespan=lifespan)
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


@app.get("/{full_path:path}")
def serve_spa(full_path: str):
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API route not found")

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
