"""Robust, self-restarting server watchdog for Indian Equities Mission Control.

Safely handles both python.exe (console) and pythonw.exe (windowless),
kills zombie processes on port 8000 before binding, and redirects
all logs to data/server.log so no stdout/stderr crashes ever occur.
"""

import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_EXE = os.path.join(SCRIPT_DIR, "TradingAgents", ".venv", "Scripts", "python.exe")
LOG_DIR = os.path.join(SCRIPT_DIR, "data")
LOG_FILE = os.path.join(LOG_DIR, "server.log")
HOST = "127.0.0.1"
PORT = 8000


def ensure_logging():
    """Ensures stdout and stderr are always valid file handles even under pythonw."""
    os.makedirs(LOG_DIR, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        f = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f


def free_port(port: int):
    """Kills any lingering process on the specified port to prevent [Errno 10048]."""
    try:
        if sys.platform == "win32":
            cmd = f'for /f "tokens=5" %a in (\'netstat -aon ^| findstr ":{port}" ^| findstr "LISTENING"\' ) do taskkill /F /PID %a'
            subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        pass


def main():
    ensure_logging()
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [WATCHDOG] Initializing Mission Control watchdog...", flush=True)

    while True:
        # Free port 8000 in case an orphaned process exists
        free_port(PORT)
        time.sleep(1)

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [WATCHDOG] Starting uvicorn on {HOST}:{PORT}...", flush=True)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as out_f:
                proc = subprocess.Popen(
                    [
                        PYTHON_EXE,
                        "-m", "uvicorn",
                        "indian_equity_agent.web.app:app",
                        "--host", HOST,
                        "--port", str(PORT),
                    ],
                    cwd=SCRIPT_DIR,
                    stdout=out_f,
                    stderr=out_f,
                )
                proc.wait()
                code = proc.returncode
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [WATCHDOG] Process exited with code {code}. Restarting in 3s...", flush=True)
        except KeyboardInterrupt:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [WATCHDOG] Stopping watchdog.", flush=True)
            break
        except Exception as e:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [WATCHDOG] Exception: {e}. Restarting in 3s...", flush=True)

        time.sleep(3)


if __name__ == "__main__":
    main()
