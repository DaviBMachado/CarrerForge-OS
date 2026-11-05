@echo off
setlocal

cd /d "%~dp0"

echo [CareerForge OS] Initializing Monolith Runtime...

:: 0. Configure git hooks for pipeline protection
if exist ".git" (
    git config core.hooksPath .githooks >nul 2>&1
)

:: 1. Verify / Create Python virtual environment
if not exist ".venv" (
    echo [CareerForge OS] Virtual environment not found. Creating .venv...
    python -m venv .venv
    if errorlevel 1 (
        echo [CareerForge OS] Failed to create virtual environment. Ensure Python 3.11+ is installed.
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat

:: 2. Check and install backend dependencies
echo [CareerForge OS] Verifying backend dependencies...
python -m pip install -q -r backend\requirements.txt

:: 3. Run database migrations
echo [CareerForge OS] Running database migrations...
python -m alembic -c backend\alembic.ini upgrade head

:: 4. Check frontend dependencies and build production bundle if missing
if not exist "frontend\node_modules" (
    echo [CareerForge OS] Installing frontend dependencies...
    cd frontend
    call npm install
    cd ..
)

if not exist "backend\app\static\index.html" (
    echo [CareerForge OS] Frontend production bundle not found. Building frontend...
    cd frontend
    call npm run build
    cd ..
)

:: 5. Launch browser and FastAPI server
echo [CareerForge OS] Launching default browser to http://localhost:8000 ...
start "" "http://localhost:8000"

echo [CareerForge OS] Starting FastAPI backend on http://127.0.0.1:8000 ...
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

endlocal
