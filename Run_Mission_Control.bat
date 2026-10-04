@echo off
title GROWW AI Trading Mission Control
cd /d "d:\Trading"
echo ============================================================
echo   GROWW AI Trading Mission Control Server
echo ============================================================
echo.
echo Freeing port 8000 if occupied...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo Starting server on http://127.0.0.1:8000 ...
start http://127.0.0.1:8000
"d:\Trading\TradingAgents\.venv\Scripts\python.exe" -m uvicorn indian_equity_agent.web.app:app --host 127.0.0.1 --port 8000
pause
