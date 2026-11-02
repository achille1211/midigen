"""`midigen ui`: open midigen in the browser.

Uses the midigen-ui web app if its folder is found (MIDIGEN_UI_DIR, a `midigen-ui` folder next to this project,
or `ui/` inside it); otherwise starts the REST service from integrations/ and opens its interactive docs page.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]  # .../midigen (for an editable / source install)


def find_ui_dir() -> Path | None:
    env = os.environ.get("MIDIGEN_UI_DIR")
    cands = ([Path(env).expanduser()] if env else []) + [PROJECT.parent / "midigen-ui", PROJECT / "ui",
                                                         Path.cwd() / "midigen-ui"]
    return next((c.resolve() for c in cands if (c / "app.py").is_file()), None)


def _open_later(url: str, delay: float = 1.5):
    threading.Timer(delay, lambda: webbrowser.open(url)).start()


def command(args, host: str, port: int, open_browser: bool) -> tuple[list[str], str, dict] | None:
    """Return (argv, url, extra env) for what `midigen ui` would launch, or None if nothing is available."""
    ui = find_ui_dir()
    if ui:
        return ([sys.executable, str(ui / "app.py"), "--host", host, "--port", str(port)],
                f"http://{'localhost' if host == '127.0.0.1' else host}:{port}/",
                {"MIDIGEN_SRC": str(Path(__file__).resolve().parents[1])})
    if all(importlib.util.find_spec(m) for m in ("fastapi", "uvicorn")) and importlib.util.find_spec("midigen_integrations"):
        return ([sys.executable, "-m", "midigen_integrations.service", "--host", host, "--port", str(port)],
                f"http://{'localhost' if host == '127.0.0.1' else host}:{port}/docs", {})
    return None


def run(args) -> int:
    plan = command(args, args.host, args.port, not args.no_browser)
    if plan is None:
        print("No web UI available: the midigen-ui folder was not found and the web service is not installed.\n"
              "  fix: run the installer again (python3 install.py), or put the midigen-ui folder next to midigen\n"
              "  meanwhile, the command line works: midigen new --genre house -o mysong", file=sys.stderr)
        return 1
    argv, url, env = plan
    if "midigen_integrations.service" in argv:
        print("The midigen-ui web app was not found, so this opens the midigen web service instead:\n"
              "an interactive API page where you can try generate/edit/export from the browser.")
    print(f"Opening {url}   (press Ctrl+C here to stop)", flush=True)
    if args.dry_run:
        print(" ".join(argv))
        return 0
    if not args.no_browser:
        _open_later(url)
    try:
        return subprocess.call(argv, env={**os.environ, **env})
    except KeyboardInterrupt:
        return 0


def add_parser(sub):
    p = sub.add_parser("ui", help="open midigen in your web browser")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766)  # 8765 is the REST service
    p.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    p.add_argument("--dry-run", action="store_true", help="print what would be started and exit")
    p.set_defaults(fn=lambda args: sys.exit(run(args)))
