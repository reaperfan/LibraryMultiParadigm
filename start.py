"""Indítószkript a teljes rendszerhez (helyi fejlesztés, Windows/Linux/macOS).

Elindítja külön folyamatként:
  * a FastAPI-backendet (uvicorn, --reload nélkül),
  * a Streamlit-frontendet,
  * a karbantartó időzített ellenőrzését (``maintenance.controller run``),
    amely ENGEDÉLY NÉLKÜL csak ellenőriz – adatbázistörlést vagy -cserét ez a
    szkript nem engedélyez.

Leállítás: Ctrl+C – minden folyamat leáll.
Használat: ``python start.py [--no-maintenance] [--backend-port 8000] [--frontend-port 8501]``
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--frontend-port", type=int, default=8501)
    parser.add_argument("--no-maintenance", action="store_true", help="a karbantartó ellenőrzés kihagyása")
    args = parser.parse_args()

    env = {
        **os.environ,
        "BACKEND_URL": f"http://127.0.0.1:{args.backend_port}",
        "MAINT_BACKEND_URL": f"http://127.0.0.1:{args.backend_port}",
        "PYTHONUNBUFFERED": "1",
    }
    commands = {
        "backend": [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.backend_port),
        ],
        "frontend": [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "frontend/app.py",
            "--server.port",
            str(args.frontend_port),
            "--server.headless",
            "true",
        ],
    }
    if not args.no_maintenance:
        commands["maintenance"] = [sys.executable, "-m", "maintenance.controller", "run"]

    procs: dict[str, subprocess.Popen] = {}
    try:
        for name, cmd in commands.items():
            procs[name] = subprocess.Popen(cmd, cwd=ROOT, env=env)
            print(f"[start] {name} elindítva (pid {procs[name].pid}): {' '.join(cmd)}")
            time.sleep(1.5)
        print(
            f"[start] Backend: http://127.0.0.1:{args.backend_port}/docs  Frontend: http://localhost:{args.frontend_port}"
        )
        print("[start] Leállítás: Ctrl+C")
        while True:
            for name, proc in procs.items():
                if proc.poll() is not None:
                    print(f"[start] {name} leállt (kód {proc.returncode}); minden folyamat leállítása")
                    raise KeyboardInterrupt
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for name, proc in procs.items():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                print(f"[start] {name} leállítva")
    return 0


if __name__ == "__main__":
    sys.exit(main())
