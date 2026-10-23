"""Local settings: which chess.com player teachess follows, what to analyse, and the coach's notes.

The player comes from the TEACHESS_USER environment variable, else from config.json's "user".
config.json is personal and git-ignored; config.example.json shows its shape.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PATH = Path(__file__).parent / "config.json"


_cache: tuple[float, dict] = (0.0, {})


def load() -> dict:
    """config.json, read again only when the file changes (it is asked for inside loops over games)."""
    global _cache
    if not PATH.exists():
        return {}
    mtime = PATH.stat().st_mtime
    if mtime != _cache[0]:
        _cache = (mtime, json.loads(PATH.read_text()))
    return _cache[1]


def save(data: dict) -> None:
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(PATH)
    global _cache
    _cache = (0.0, {})


def chess_user() -> str:
    user = os.environ.get("TEACHESS_USER") or load().get("user", "")
    if not user:
        raise SystemExit("Set your chess.com username: TEACHESS_USER=<name> or \"user\" in config.json "
                         "(see config.example.json).")
    return user.lower()
