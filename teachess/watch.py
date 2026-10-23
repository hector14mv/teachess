"""teachess watch: the lesson of every game the moment it ends, and the stop after two defeats in a row.

Runs as a loop (`teachess watch`), as one check (`--once`, what the background service runs every two minutes)
or as a macOS LaunchAgent (`teachess watch install`). It remembers in data/watch_state.json which games it already
told you about, so it works whoever synced them: this watcher, the dashboard or `teachess sync`.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import core, drills, notify
from .errors import SetupError, TeachessError

STATE = core.DATA / "watch_state.json"
LOG = core.DATA / "watch.log"
LABEL = "dev.teachess.watch"
POLL_SECONDS = 120
FRESH_HOURS = 3  # a game first seen later than this (the laptop was asleep) is not worth a notification
BIN = core.ROOT / "bin" / "teachess"


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def lesson(g: dict, evals: dict, report: dict, due: int) -> dict:
    d = core.describe(g)
    r = core.review(g, evals, report)
    body = r["verdict"]
    if r["first"] and r["first"]["repeated"]:
        body += f" Está en tus tarjetas: {due} para hoy."
    return {"type": "game", "title": f"{d['result']} con {d['side']} · {d['opening']}", "body": body,
            "url": g["url"]}


def check(live: bool = True, at: datetime | None = None, send=notify.send) -> list[dict]:
    """Sync, then tell about each rapid game not told yet and about a losing streak once per streak."""
    if live:
        core.sync(log=lambda *_: None)
    games, report, evals = core.load_games(), core.load_report(), core.load_evals()
    at = at or core.now()
    rapid = [g for g in games if g["time_class"] == "rapid" and core.end_dt(g) <= at]
    state = load_state()
    if not rapid:
        return []
    if "last_end" not in state:
        # First run: start from now instead of announcing the whole archive
        state["last_end"] = rapid[-1]["end"]
        STATE.write_text(json.dumps(state))
        return []

    last = datetime.fromisoformat(state["last_end"])
    new = [g for g in rapid if core.end_dt(g) > last
           and (at - core.end_dt(g)).total_seconds() < FRESH_HOURS * 3600]
    due = drills.queue(report)[1]["due"]
    events = [lesson(g, evals, report, due) for g in new[-3:]]
    if core.end_dt(rapid[-1]) > last:
        state["last_end"] = rapid[-1]["end"]

    t = core.today(games, report, at=at)
    if t["state"] == "streak" and state.get("stopped_after") != t["last"]["url"]:
        events.append({"type": "stop", "title": f"Llevas {t['losses_in_a_row']} derrotas seguidas. Para aquí.",
                       "body": f"{t['advice']} Mientras, repasa tus tarjetas: teachess drill.", "url": None})
        state["stopped_after"] = t["last"]["url"]

    STATE.write_text(json.dumps(state))
    for e in events:
        send(e["title"], e["body"], e["url"])
    return events


def loop(live: bool = True) -> None:
    print(f"Vigilando chess.com cada {POLL_SECONDS // 60} minutos. Ctrl+C para salir.", flush=True)
    while True:
        try:
            check(live=live)
        except TeachessError as e:  # chess.com down or rate-limiting: try again next round
            print(f"  ▸ {e}", flush=True)
        time.sleep(POLL_SECONDS)


# --- the background service (macOS LaunchAgent) ----------------------------------------------

def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def launchctl(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *argv], capture_output=True, text=True)


def domain() -> str:
    return f"gui/{os.getuid()}"


def install() -> dict:
    if sys.platform != "darwin":
        raise SetupError("El servicio de fondo es para macOS. En Linux, pon «teachess watch --once» en un "
                         "temporizador de systemd o en cron cada 2 minutos.")
    path = plist_path()
    plist = {
        "Label": LABEL,
        "ProgramArguments": [str(BIN), "watch", "--once"],
        "StartInterval": POLL_SECONDS,
        "RunAtLoad": True,
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
        # launchd starts jobs with a bare PATH; Stockfish usually lives in Homebrew's
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
    }
    text = plistlib.dumps(plist)
    status = "unchanged" if path.exists() and path.read_bytes() == text else ("updated" if path.exists() else "created")
    core.DATA.mkdir(exist_ok=True)
    if status != "unchanged":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text)
        launchctl("bootout", domain(), str(path))  # not loaded yet is fine
    if status != "unchanged" or launchctl("print", f"{domain()}/{LABEL}").returncode:
        done = launchctl("bootstrap", domain(), str(path))
        if done.returncode:
            raise SetupError(f"launchctl bootstrap falló: {(done.stderr or done.stdout).strip()}")
    return {"status": status, "plist": str(path), "log": str(LOG)}


def uninstall() -> dict:
    path = plist_path()
    if not path.exists():
        return {"status": "absent", "plist": str(path)}
    launchctl("bootout", domain(), str(path))
    path.unlink()
    return {"status": "removed", "plist": str(path)}


def status() -> dict:
    loaded = sys.platform == "darwin" and launchctl("print", f"{domain()}/{LABEL}").returncode == 0
    log = LOG.read_text().splitlines()[-5:] if LOG.exists() else []
    return {"installed": plist_path().exists(), "running": loaded, "state": load_state(), "log": log}
