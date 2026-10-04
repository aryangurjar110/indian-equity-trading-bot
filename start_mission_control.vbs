Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonExe = scriptDir & "\TradingAgents\.venv\Scripts\python.exe"
launcherPy = scriptDir & "\launcher.py"

WshShell.CurrentDirectory = scriptDir
WshShell.Run """" & pythonExe & """ """ & launcherPy & """", 0, False
