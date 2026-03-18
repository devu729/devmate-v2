@echo off
setlocal EnableDelayedExpansion

echo.
echo   ____            __  __       _        
echo  ^|  _ \  _____   _^|  \/  ^| __ _^| ^|_ ___ 
echo  ^| ^| ^| ^|/ _ \ \ / / ^|\/^| ^|/ _` ^| __/ _ \
echo  ^| ^|_^| ^|  __/\ V /^| ^|  ^| ^| (_^| ^| ^|^|  __/
echo  ^|____/ \___^|  \_/ ^|_^|  ^|_^|\__,_^|\__\___ ^|  v2
echo.
echo  Context-aware AI coding assistant — powered by DigitalOcean Gradient
echo.

REM ── 1. Check for .env ───────────────────────────────────────────────────────
if not exist ".env" (
    echo [1/3] .env not found — copying from .env.example
    copy .env.example .env >nul
    echo.
    echo  WARNING: Open .env and add your DO_GRADIENT_API_KEY, then re-run this script.
    echo  Get your key at: https://cloud.digitalocean.com/gradient
    echo.
    pause
    exit /b 1
)

REM Parse DO_GRADIENT_API_KEY from .env
set "API_KEY="
for /f "tokens=1,2 delims==" %%A in (.env) do (
    if "%%A"=="DO_GRADIENT_API_KEY" set "API_KEY=%%B"
)

if "!API_KEY!"=="" (
    echo [ERROR] DO_GRADIENT_API_KEY is empty in .env
    echo         Get your key at https://cloud.digitalocean.com/gradient
    pause
    exit /b 1
)
echo [1/3] .env validated

REM ── 2. Check for Docker ─────────────────────────────────────────────────────
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker is not running or not installed.
    echo         Install Docker Desktop from https://docker.com and start it.
    pause
    exit /b 1
)
echo [2/3] Docker detected and running

REM ── 3. Build + start ────────────────────────────────────────────────────────
echo [3/3] Building and starting DevMate v2...
docker compose up --build -d

if errorlevel 1 (
    echo [ERROR] docker compose failed. Check output above.
    pause
    exit /b 1
)

REM Wait for health check
echo.
echo  Waiting for service...
set /a attempts=0
:wait_loop
set /a attempts+=1
curl -sf http://localhost:8000/health >nul 2>&1
if not errorlevel 1 goto healthy
if !attempts! geq 30 (
    echo  Service did not become healthy. Check: docker compose logs devmate
    pause
    exit /b 1
)
timeout /t 2 /nobreak >nul
goto wait_loop

:healthy
echo.
echo  ═══════════════════════════════════════════════════
echo    DevMate v2 is live!
echo    ^→ http://localhost:8000
echo  ═══════════════════════════════════════════════════
echo.
echo  Paste any public GitHub URL to get started.
echo  Stop with: docker compose down
echo.

start http://localhost:8000
pause
