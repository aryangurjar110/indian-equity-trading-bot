@echo off
cd /d "%~dp0"
echo Launching Groww Mission Control in background...
wscript.exe "%~dp0start_mission_control.vbs"
