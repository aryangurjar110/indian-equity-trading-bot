"""Indian Equities Mission Control Background Launcher.

Detaches the uvicorn web server as an independent Windows process,
redirects logs to data/server.log, and opens the dashboard in the default browser.
"""

import os
import subprocess
import sys
import time
import webbrowser

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_EXE = os.path.join(SCRIPT_DIR, "TradingAgents", ".venv", "Scripts", "python.exe")
LOG_DIR = os.path.join(SCRIPT_DIR, "data")
LOG_FILE = os.path.join(LOG_DIR, "server.log")
os.makedirs(LOG_DIR, exist_ok=True)

# 1. Terminate any previous process on port 8000
try:
    cmd = 'for /f "tokens=5" %a in (\'netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"\' ) do taskkill /F /PID %a'
    subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
except Exception:
    pass

time.sleep(1)

# 2. Launch uvicorn completely detached
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

with open(LOG_FILE, "a", encoding="utf-8") as out:
    proc = subprocess.Popen(
        [
            PYTHON_EXE,
            "-m", "uvicorn",
            "indian_equity_agent.web.app:app",
            "--host", "127.0.0.1",
            "--port", "8000",
        ],
        cwd=SCRIPT_DIR,
        stdout=out,
        stderr=out,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )

# 3. Wait 2.5 seconds for server startup
time.sleep(2.5)

# 4. Open browser
webbrowser.open("http://127.0.0.1:8000")
