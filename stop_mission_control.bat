@echo off
echo Stopping Indian Equities Mission Control on port 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo Terminating PID %%a listening on port 8000...
    taskkill /F /PID %%a
)
echo Mission Control stopped.
pause
