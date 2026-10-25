"""Where teachess keeps your games and settings: a folder of its own, outside the code.

The code may live in a git checkout or in a Homebrew cellar that each upgrade replaces, so your games,
evaluations, drill progress and config.json live in TEACHESS_HOME, by default
~/Library/Application Support/teachess on macOS and $XDG_DATA_HOME/teachess (~/.local/share/teachess) elsewhere.
Versions before 0.1.0 kept them inside the checkout (data/ and config.json); `migrate` moves them once.
"""

from __future__ import annotations

import contextlib
import errno
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


def _move(src: Path, dst: Path) -> None:
    """Move a file or folder so that `dst` appears only once it is complete: a rename on the same disk, else a
    copy to `dst.partial` renamed into place before the source goes."""
    try:
        os.rename(src, dst)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    part = dst.with_name(dst.name + ".partial")
    if part.is_dir():
        shutil.rmtree(part)
    elif part.exists():
        part.unlink()
    if src.is_dir():
        shutil.copytree(src, part)
    else:
        shutil.copy2(src, part)
    os.rename(part, dst)
    if src.is_dir():
        shutil.rmtree(src)
    else:
        src.unlink()


def migrate(log=print) -> bool:
    """Move a checkout's data/ and config.json into HOME, once. True when something moved.

    Never with TEACHESS_HOME set (an explicit home, as in tests) and never over what HOME already holds. A
    watcher or rebuild of the old version still running holds the old folder's locks: the move waits for them.
    Each item lands whole (see `_move`) in one staging folder, resumed if a move was cut short, and the folder
    becomes DATA in one rename, so the watcher never sees half of it."""
    if os.environ.get("TEACHESS_HOME"):
        return False
    old_data, old_config, staging = LEGACY / "data", LEGACY / "config.json", HOME / "data.migrating"
    data_due = not DATA.exists() and ((old_data / "games.jsonl").exists() or staging.exists())
    config_due = not CONFIG.exists() and old_config.exists()
    if not data_due and not config_due:
        return False
    HOME.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as held:
        for path in (HOME / ".migrate.lock", old_data / "watch.lock", old_data / "data.lock"):
            if path.parent.exists():
                lock = held.enter_context(open(path, "a"))
                fcntl.flock(lock, fcntl.LOCK_EX)
        moved = False
        if not DATA.exists() and ((old_data / "games.jsonl").exists() or staging.exists()):
            staging.mkdir(exist_ok=True)
            for name in LEGACY_ITEMS:
                if (old_data / name).exists() and not (staging / name).exists():
                    _move(old_data / name, staging / name)
            os.rename(staging, DATA)
            moved = True
        if not CONFIG.exists() and old_config.exists():
            _move(old_config, CONFIG)
            moved = True
    if moved:
        log(f"Tus partidas y tu configuración están ahora en {HOME}.")
        if sys.platform == "darwin" and (Path.home() / "Library" / "LaunchAgents" / "dev.teachess.watch.plist").exists():
            log("Ejecuta «teachess watch install» para que el aviso de fondo escriba su registro allí.")
    return moved


def command() -> str:
    """The teachess command that background jobs start (the watcher's LaunchAgent, the MCP server): a git
    checkout's own bin/teachess, else the one on PATH (Homebrew's /opt/homebrew/bin/teachess, which survives
    upgrades), else the script that is running."""
    checkout = LEGACY / "bin" / "teachess"
    if checkout.exists():
        return str(checkout)
    return shutil.which("teachess") or str(Path(sys.argv[0]).resolve())
