from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from backend.app.db import init_db
from backend.app.routers.compiler import router as compiler_router
from backend.app.routers.profile import router as profile_router
from backend.app.routers.settings import router as settings_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="CareerForge OS", version="0.1.0", lifespan=lifespan)
app.include_router(compiler_router, prefix="/api")
app.include_router(profile_router, prefix="/api")
app.include_router(settings_router, prefix="/api")

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


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

    target_file = (FRONTEND_DIST / full_path).resolve()
    # Prevent directory traversal attacks
    if (
        full_path
        and target_file.is_file()
        and str(target_file).startswith(str(FRONTEND_DIST.resolve()))
    ):
        return FileResponse(target_file)

    index_file = FRONTEND_DIST / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)

    if not full_path or full_path == "":
        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "app": "CareerForge OS",
                "message": "Backend running. Frontend build not found at frontend/dist.",
            },
        )

    raise HTTPException(status_code=404, detail="Not found")
