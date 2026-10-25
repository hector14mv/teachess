"""Where teachess keeps your games and settings: a folder of its own, outside the code.

The code may live in a git checkout or in a Homebrew cellar that each upgrade replaces, so your games,
evaluations, drill progress and config.json live in TEACHESS_HOME, by default
~/Library/Application Support/teachess on macOS and $XDG_DATA_HOME/teachess (~/.local/share/teachess) elsewhere.
Versions before 0.1.0 kept them inside the checkout (data/ and config.json); `migrate` moves them once.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import sys
from pathlib import Path


def _home() -> Path:
    if os.environ.get("TEACHESS_HOME"):
        return Path(os.environ["TEACHESS_HOME"]).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "teachess"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "teachess"


HOME = _home()
DATA = HOME / "data"
CONFIG = HOME / "config.json"

# A git checkout's own folder: where versions before 0.1.0 kept the data
LEGACY = Path(__file__).resolve().parent.parent
# Only what teachess wrote there: anything else in the old data/ (your own files) stays where it is
LEGACY_ITEMS = ("raw", "games.jsonl", "evals.json", "report.json", "deep.json", "player", "drill_state.json",
                "watch_state.json", "watch.log")


def migrate(log=print) -> bool:
    """Move a checkout's data/ and config.json into HOME, once. True when something moved.

    Never with TEACHESS_HOME set (an explicit home, as in tests) and never over what HOME already holds. The
    data moves under a lock into one staging folder (resumed if a move was cut short) and lands in one rename,
    so the watcher never sees half a folder."""
    if os.environ.get("TEACHESS_HOME"):
        return False
    old_data, old_config, staging = LEGACY / "data", LEGACY / "config.json", HOME / "data.migrating"
    data_due = not DATA.exists() and ((old_data / "games.jsonl").exists() or staging.exists())
    config_due = not CONFIG.exists() and old_config.exists()
    if not data_due and not config_due:
        return False
    HOME.mkdir(parents=True, exist_ok=True)
    with open(HOME / ".migrate.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        moved = False
        if not DATA.exists() and ((old_data / "games.jsonl").exists() or staging.exists()):
            staging.mkdir(exist_ok=True)
            for name in LEGACY_ITEMS:
                if (old_data / name).exists() and not (staging / name).exists():
                    shutil.move(str(old_data / name), str(staging / name))
            staging.replace(DATA)
            moved = True
        if not CONFIG.exists() and old_config.exists():
            shutil.move(str(old_config), str(CONFIG))
            moved = True
    if moved:
        log(f"Tus partidas y tu configuración están ahora en {HOME}.")
    return moved


def command() -> str:
    """The teachess command that background jobs start (the watcher's LaunchAgent, the MCP server): a git
    checkout's own bin/teachess, else the one on PATH (Homebrew's /opt/homebrew/bin/teachess, which survives
    upgrades), else the script that is running."""
    checkout = LEGACY / "bin" / "teachess"
    if checkout.exists():
        return str(checkout)
    return shutil.which("teachess") or str(Path(sys.argv[0]).resolve())
