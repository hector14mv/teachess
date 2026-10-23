"""Evaluate every unique position of the first PLIES plies of recent rapid games with Stockfish.

Usage: .venv/bin/python engine_eval.py [since=YYYY-MM-DD] [plies=24] [depth=14]
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
from pathlib import Path

import chess
import chess.engine

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "evals.json"
SINCE = "2025-10-15"
PLIES = 24
DEPTH = 14

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
    for line in open(ROOT / "data" / "games.jsonl"):
        g = json.loads(line)
        if g["time_class"] != "rapid" or g["end"] < SINCE:
            continue
        board = chess.Board()
        todo.add(key(board))
        for san in g["sans"][:PLIES]:
            board.push_san(san)
            todo.add(key(board))
    todo -= evals.keys()
    print(f"{len(todo)} positions to evaluate at depth {DEPTH}", flush=True)
    with Pool(max(1, (os.cpu_count() or 2) - 1)) as pool:
        for i, (fen, ev) in enumerate(pool.imap_unordered(partial(evaluate, depth=DEPTH), sorted(todo), chunksize=32), 1):
            evals[fen] = ev
            if i % 1000 == 0:
                print(f"  {i}/{len(todo)}", flush=True)
                OUT.write_text(json.dumps(evals))
    OUT.write_text(json.dumps(evals))
    print(f"done: {len(evals)} positions in {OUT.name}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        SINCE = sys.argv[1]
    if len(sys.argv) > 2:
        PLIES = int(sys.argv[2])
    if len(sys.argv) > 3:
        DEPTH = int(sys.argv[3])
    main()
