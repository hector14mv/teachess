"""Flatten data/raw/*.json into data/games.jsonl: one row per standard game, from the player's side.

Usage: .venv/bin/python parse_games.py [username]   (default: TEACHESS_USER or config.json)
"""

from __future__ import annotations

import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import chess.pgn

import config

USER = ""  # set by main() or by the caller before parse()
ROOT = Path(__file__).parent
MADRID = ZoneInfo("Europe/Madrid")
CLK = re.compile(r"\[%clk (\d+):(\d+):([\d.]+)\]")
DRAWS = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}


def opening_name(eco_url: str) -> str:
    slug = eco_url.rsplit("/", 1)[-1] if eco_url else ""
    return slug.replace("-", " ")


STANDARD = chess.STARTING_FEN.split()[:2]  # placement and side to move


def standard_start(g: dict) -> bool:
    """False for thematic or from-position games, which cannot be replayed from the usual first move."""
    return (g.get("initial_setup") or chess.STARTING_FEN).split()[:2] == STANDARD


def parse(g: dict) -> dict | None:
    if g.get("rules") != "chess" or "pgn" not in g or not standard_start(g):
        return None
    me_white = g["white"]["username"].lower() == USER
    me, opp = (g["white"], g["black"]) if me_white else (g["black"], g["white"])
    if me["result"] == "win":
        score, how = 1.0, opp["result"]
    elif me["result"] in DRAWS:
        score, how = 0.5, me["result"]
    else:
        score, how = 0.0, me["result"]

    game = chess.pgn.read_game(io.StringIO(g["pgn"]))
    if game is None or game.board().fen() != chess.STARTING_FEN:
        return None
    sans, my_clocks, opp_clocks = [], [], []
    board = game.board()
    for i, node in enumerate(game.mainline()):
        sans.append(board.san(node.move))
        board.push(node.move)
        m = CLK.search(node.comment or "")
        if m:
            secs = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
            (my_clocks if (i % 2 == 0) == me_white else opp_clocks).append(round(secs, 1))

    acc = g.get("accuracies") or {}
    end = datetime.fromtimestamp(g["end_time"], tz=timezone.utc).astimezone(MADRID)
    return {
        "url": g["url"],
        "end": end.isoformat(),
        "time_class": g["time_class"],
        "time_control": g["time_control"],
        "rated": g.get("rated", True),
        "color": "white" if me_white else "black",
        "my_rating": me["rating"],
        "opp_rating": opp["rating"],
        "opp": opp["username"],
        "score": score,
        "how": how,
        "eco": game.headers.get("ECO", ""),
        "opening": opening_name(g.get("eco", "")),
        "plies": len(sans),
        "sans": sans,
        "my_clocks": my_clocks,
        "opp_clocks": opp_clocks[:5],
        "my_acc": acc.get("white" if me_white else "black"),
        "opp_acc": acc.get("black" if me_white else "white"),
    }


def main() -> None:
    rows = []
    for f in sorted((ROOT / "data" / "raw").glob("*.json")):
        for g in json.loads(f.read_text())["games"]:
            r = parse(g)
            if r:
                rows.append(r)
    rows.sort(key=lambda r: datetime.fromisoformat(r["end"]))  # by the moment, not the offset's text
    with open(ROOT / "data" / "games.jsonl", "w") as out:
        for r in rows:
            out.write(json.dumps(r) + "\n")
    print(f"{len(rows)} games -> data/games.jsonl")


if __name__ == "__main__":
    USER = sys.argv[1].lower() if len(sys.argv) > 1 else config.chess_user()
    main()
