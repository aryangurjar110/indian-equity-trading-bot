@echo off
echo ============================================================
echo  Installing Mission Control as Windows Auto-Start Service
echo ============================================================
echo.

set "SCRIPT_DIR=%~dp0"
set "VBS_PATH=%SCRIPT_DIR%start_mission_control.vbs"
set "TASK_NAME=GrowwMissionControl"

echo Removing old scheduled task (if exists)...
schtasks /Delete /TN "%TASK_NAME%" /F >nul 2>&1

echo Creating scheduled task to run at user logon...
schtasks /Create /TN "%TASK_NAME%" /TR "wscript.exe \"%VBS_PATH%\"" /SC ONLOGON /RL HIGHEST /F

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ============================================================
    echo  SUCCESS! Mission Control will now auto-start every time
    echo  you log into Windows. No terminal needed.
    echo.
    echo  To start it right now, double-click:
    echo    %VBS_PATH%
    echo.
    echo  To remove auto-start later, run:
    echo    schtasks /Delete /TN "%TASK_NAME%" /F
    echo ============================================================
) else (
    echo.
    echo ERROR: Could not create scheduled task.
    echo Try running this script as Administrator.
)

echo.
pause
