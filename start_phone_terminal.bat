@echo off
title GROWW TERMINAL - Phone Connect & Local Server
echo ========================================================
echo   GROWW TRADING TERMINAL - MOBILE ACCESS LAUNCHER
echo ========================================================
echo.
echo Starting trading server...
start /b "" "d:\Trading\TradingAgents\.venv\Scripts\python.exe" -m uvicorn indian_equity_agent.web.app:app --host 0.0.0.0 --port 8000
timeout /t 3 /nobreak >nul

echo Starting secure Cloudflare Tunnel for your phone...
echo.
echo Your phone link will appear below in 5 seconds.
echo (Open this HTTPS link in your phone browser - works on 5G/Wi-Fi/anywhere!)
echo ========================================================
echo.
"d:\Trading\cloudflared.exe" tunnel --url http://127.0.0.1:8000
pause
