"""Evaluate every unique position of the recent games of one time control with Stockfish.

Usage: .venv/bin/python engine_eval.py [since=YYYY-MM-DD] [plies=24|all] [depth=14] [time_class=rapid]
Writes data/evals.json: {fen: {"cp": int (white POV, mate clamped to ±1000), "best": san}}.
Already-evaluated positions are kept, so reruns only add new games.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from functools import partial
from multiprocessing import Pool

import chess
import chess.engine

from . import paths, progress

OUT = paths.DATA / "evals.json"
SINCE = "2025-10-15"
PLIES = 24  # None: every move of the game
DEPTH = 14
TIME_CLASS = "rapid"

_engine = None


def key(board: chess.Board) -> str:
    # Drop move counters so transpositions share one evaluation.
    return " ".join(board.fen().split()[:4])


def evaluate(fen: str, depth: int = DEPTH) -> tuple[str, dict]:
    global _engine
    if _engine is None:
        _engine = chess.engine.SimpleEngine.popen_uci(shutil.which("stockfish"))
        _engine.configure({"Threads": 1, "Hash": 64})
    board = chess.Board(fen + " 0 1")
    if board.is_game_over():
        outcome = board.outcome()
        cp = 0 if outcome.winner is None else (1000 if outcome.winner else -1000)
        return fen, {"cp": cp, "best": None}
    info = _engine.analyse(board, chess.engine.Limit(depth=depth))
    cp = info["score"].white().score(mate_score=1000)
    cp = max(-1000, min(1000, cp))
    best = board.san(info["pv"][0]) if info.get("pv") else None
    return fen, {"cp": cp, "best": best}


def main() -> None:
    evals = json.loads(OUT.read_text()) if OUT.exists() else {}
    todo = set()
    for line in open(paths.DATA / "games.jsonl"):
        g = json.loads(line)
        if g["time_class"] != TIME_CLASS or g["end"] < SINCE:
            continue
        board = chess.Board()
        todo.add(key(board))
        for san in g["sans"][:PLIES]:
            board.push_san(san)
            todo.add(key(board))
    todo -= evals.keys()
    bar = progress.Bar("Analizando tus partidas", len(todo))
    with Pool(max(1, (os.cpu_count() or 2) - 1)) as pool:
        for i, (fen, ev) in enumerate(pool.imap_unordered(partial(evaluate, depth=DEPTH), sorted(todo), chunksize=32), 1):
            evals[fen] = ev
            bar.update(i)
            if i % 1000 == 0:
                OUT.write_text(json.dumps(evals))
    bar.close()
    OUT.write_text(json.dumps(evals))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        SINCE = sys.argv[1]
    if len(sys.argv) > 2:
        PLIES = None if sys.argv[2] == "all" else int(sys.argv[2])
    if len(sys.argv) > 3:
        DEPTH = int(sys.argv[3])
    if len(sys.argv) > 4:
        TIME_CLASS = sys.argv[4]
    main()
