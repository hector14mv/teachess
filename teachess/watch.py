"""teachess watch: the lesson of every game the moment it ends, and the stop after two defeats in a row.

Runs as a loop (`teachess watch`), as one check (`--once`, what the background service runs every two minutes)
or as a macOS LaunchAgent (`teachess watch install`). It remembers in data/watch_state.json which games it already
told you about, so it works whoever synced them: this watcher, the dashboard or `teachess sync`.
"""

from __future__ import annotations

import fcntl
import json
import os
import plistlib
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from . import core, drills, notify
from .errors import SetupError, TeachessError

STATE = core.DATA / "watch_state.json"
LOG = core.DATA / "watch.log"
LOCK = core.DATA / "watch.lock"
LABEL = "dev.teachess.watch"
POLL_SECONDS = 120
FRESH_HOURS = 3  # a game first seen later than this (the laptop was asleep) is not worth a notification
BIN = core.ROOT / "bin" / "teachess"


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):  # missing or cut short: start over from now
        return {}


def save_state(state: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE)


def lesson(g: dict, evals: dict, report: dict, due: int) -> dict:
    d = core.describe(g)
    r = core.review(g, evals, report)
    body = r["verdict"]
    if r["first"] and r["first"]["repeated"]:
        body += f" Está en tus tarjetas: {due} para hoy."
    return {"type": "game", "title": f"{d['result']} con {d['side']} · {d['opening']}", "body": body,
            "url": g["url"]}


def check(live: bool = True, at: datetime | None = None, send=notify.send) -> list[dict]:
    """Sync, then tell about each rapid game not told yet and about a losing streak once per streak.

    Only one check runs at a time (the service and a foreground watcher share the state), and the state moves
    on only after the notifications went out, so a crash repeats a notification rather than losing it."""
    core.DATA.mkdir(exist_ok=True)
    with open(LOCK, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return []  # another check is running; it will tell
        if live:
            core.sync(log=lambda *_: None)
        return _check(at or core.now(), send)


def _check(at: datetime, send) -> list[dict]:
    games, report, evals = core.load_games(), core.load_report(), core.load_evals()
    # Sorted by the moment, not by the ISO text: the offset changes with daylight saving time
    rapid = sorted((g for g in games if g["time_class"] == "rapid" and core.end_dt(g) <= at), key=core.end_dt)
    state = load_state()
    if "last_end" not in state:
        # First run: start from the latest game (or from now) instead of announcing the whole archive
        state["last_end"] = rapid[-1]["end"] if rapid else at.isoformat()
        save_state(state)
        return []

    last = datetime.fromisoformat(state["last_end"])
    new = [g for g in rapid if core.end_dt(g) > last
           and (at - core.end_dt(g)).total_seconds() < FRESH_HOURS * 3600]
    due = drills.queue(report)[1]["due"]
    events = [lesson(g, evals, report, due) for g in new[-3:]]
    if len(new) > 3:
        events.insert(0, {"type": "more", "title": f"{len(new) - 3} partidas más desde el último aviso",
                          "body": "Míralas con teachess games.", "url": None})

    t = core.today(games, report, at=at)
    streak_start = None
    if t["state"] == "streak":
        streak_start = rapid[-t["losses_in_a_row"]]["url"]  # the streak is known by its first defeat
        if state.get("streak") != streak_start:
            events.append({"type": "stop", "title": f"Llevas {t['losses_in_a_row']} derrotas seguidas. Para aquí.",
                           "body": f"{t['advice']} Mientras, repasa tus tarjetas: teachess drill.", "url": None})

    for e in events:
        send(e["title"], e["body"], e["url"])
    if rapid and core.end_dt(rapid[-1]) > last:
        state["last_end"] = rapid[-1]["end"]
    if streak_start:
        state["streak"] = streak_start
    save_state(state)
    return events


def loop(live: bool = True) -> None:
    print(f"Vigilando chess.com cada {POLL_SECONDS // 60} minutos. Ctrl+C para salir.", flush=True)
    while True:
        try:
            check(live=live)
        except TeachessError as e:  # chess.com down or rate-limiting: try again next round
            print(f"  ▸ {e}", flush=True)
        except Exception:  # nobody watches this loop: log the failure and keep going
            traceback.print_exc()
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
    done = launchctl("bootout", domain(), str(path))
    if done.returncode and launchctl("print", f"{domain()}/{LABEL}").returncode == 0:
        raise SetupError(f"launchctl bootout falló y el servicio sigue cargado: {(done.stderr or done.stdout).strip()}")
    path.unlink()
    return {"status": "removed", "plist": str(path)}


def status() -> dict:
    loaded = sys.platform == "darwin" and launchctl("print", f"{domain()}/{LABEL}").returncode == 0
    log = LOG.read_text().splitlines()[-5:] if LOG.exists() else []
    return {"installed": plist_path().exists(), "running": loaded, "state": load_state(), "log": log}
