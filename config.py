"""Local settings: which chess.com player teachess follows and the coach's notes for the dashboard.

The player comes from the TEACHESS_USER environment variable, else from config.json's "user".
config.json is personal and git-ignored; config.example.json shows its shape.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PATH = Path(__file__).parent / "config.json"


def load() -> dict:
    return json.loads(PATH.read_text()) if PATH.exists() else {}


def chess_user() -> str:
    user = os.environ.get("TEACHESS_USER") or load().get("user", "")
    if not user:
        raise SystemExit("Set your chess.com username: TEACHESS_USER=<name> or \"user\" in config.json "
                         "(see config.example.json).")
    return user.lower()
