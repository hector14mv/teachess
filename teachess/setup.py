"""teachess setup: choose, for this player, how much of the history to analyse, then analyse it move by move.

The options come from the player's own archive: the time control they play most, how many games each window
holds, and how long Stockfish would take on this machine (measured on a sample, not guessed). The recommended
window is the shortest one with enough games to find patterns, because recent games say how you play now.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import date

import chess
import chess.engine

from . import core
from .errors import SetupError, UsageError

MIN_GAMES = 300  # enough games of one time control to find repeated mistakes
OFFERED = ["3m", "1y", "3y", "all"]
LABELS = {"3m": "Últimos 3 meses", "6m": "Últimos 6 meses", "1y": "Último año", "2y": "Últimos 2 años",
          "3y": "Últimos 3 años", "all": "Todo"}
SAMPLE = 40


def per_position(todo: list[str]) -> float:
    """Seconds one Stockfish takes per position here, measured on a sample that is kept, not thrown away."""
    path = shutil.which("stockfish")
    if not path:
        raise SetupError("Stockfish no está instalado (brew install stockfish o apt install stockfish).")
    if not todo:
        return 0.0
    sample = todo[:: max(1, len(todo) // SAMPLE)][:SAMPLE]
    evals = core.load_evals()
    engine = chess.engine.SimpleEngine.popen_uci(path)
    start = time.monotonic()
    try:
        engine.configure({"Threads": 1, "Hash": 64})
        for fen in sample:
            board = chess.Board(fen + " 0 1")
            if board.is_game_over():
                continue
            info = engine.analyse(board, chess.engine.Limit(depth=core.DEPTH))
            cp = max(-1000, min(1000, info["score"].white().score(mate_score=1000)))
            evals[fen] = {"cp": cp, "best": board.san(info["pv"][0]) if info.get("pv") else None}
    finally:
        engine.quit()
    seconds = (time.monotonic() - start) / len(sample)
    core.EVALS.write_text(json.dumps(evals))
    return seconds


def options(games: list[dict], time_class: str | None = None, measure: bool = True) -> dict:
    """The windows worth offering this player, with games, positions left and minutes for each."""
    year_ago = core.window_since("1y")
    counts = {t: sum(1 for g in games if g["time_class"] == t) for t in core.TIME_CLASSES}
    recent = {t: sum(1 for g in games if g["time_class"] == t and g["end"] >= year_ago) for t in core.TIME_CLASSES}
    tc = time_class or max(core.TIME_CLASSES, key=lambda t: (recent[t], counts[t]))
    if time_class and time_class not in core.TIME_CLASSES:
        raise UsageError(f"Ritmo desconocido: {time_class}.")
    mine = [g for g in games if g["time_class"] == tc]
    if not mine:
        raise SetupError(f"No hay partidas de {tc} en tu archivo de chess.com.")
    evals = core.load_evals()
    first_year = core.end_dt(mine[0]).year
    opts, last_n = [], None
    for w in OFFERED:
        gs = [g for g in mine if g["end"] >= core.window_since(w)]
        if not gs or len(gs) == last_n:
            continue  # nothing there, or the account is younger than this window: same games as the last one
        last_n = len(gs)
        todo = sorted(core.positions(gs) - evals.keys())
        opts.append({"window": w, "label": LABELS[w] + (f" (desde {first_year})" if w == "all" else ""),
                     "games": len(gs), "positions": len(todo), "todo": todo})
    workers = max(1, (os.cpu_count() or 2) - 1)
    pace = per_position(opts[-1]["todo"]) if measure else None
    for o in opts:
        todo = o.pop("todo")
        parallel = workers if len(todo) > core.BULK else 1
        o["minutes"] = None if pace is None else max(1, round(len(todo) * pace / parallel / 60))
    rec = next((o["window"] for o in opts if o["games"] >= MIN_GAMES), opts[-1]["window"])
    return {"time_class": tc, "games_by_time_class": counts, "options": opts, "recommended": rec,
            "ask": len(opts) > 1 and len(mine) > MIN_GAMES}


def ask(o: dict) -> str:
    n = f"{sum(o['games_by_time_class'].values()):,}".replace(",", ".")
    others = ", ".join(f"{v} de {t}" for t, v in o["games_by_time_class"].items() if v and t != o["time_class"])
    print(f"\nTienes {n} partidas en chess.com; analizo las de {o['time_class']}" + (f" (también hay {others})." if others else "."))
    print("¿Cuánto historial quieres que analice? Stockfish mira cada jugada.\n")
    for i, opt in enumerate(o["options"], 1):
        games = f"{opt['games']:,}".replace(",", ".")
        mins = "ya analizado" if not opt["positions"] else f"~{opt['minutes']} min"
        mark = "  ← recomendado" if opt["window"] == o["recommended"] else ""
        print(f"  {i}. {opt['label']:<22} {games:>6} partidas  {mins:>13}{mark}")
    default = next(i for i, opt in enumerate(o["options"], 1) if opt["window"] == o["recommended"])
    valid = [str(i) for i in range(1, len(o["options"]) + 1)]
    pick = input(f"\nElige {valid[0]}-{valid[-1]} (Enter: {default}) > ").strip() or str(default)
    while pick not in valid:
        pick = input(f"{valid[0]}-{valid[-1]} > ").strip() or str(default)
    return o["options"][int(pick) - 1]["window"]


def run(window: str | None = None, time_class: str | None = None, interactive: bool = False, log=print) -> dict:
    """Fetch the archive, pick the window (asked, given or recommended), save it and analyse it."""
    if window and window not in core.WINDOWS:
        raise UsageError(f"Ventana desconocida: {window}. Usa una de {', '.join(core.WINDOWS)}.")
    log("Descargando tus partidas de chess.com (la primera vez, mes a mes)…")
    core.sync(log=log, analyse=False)
    if core.upgrade_games():
        log("Partidas releídas enteras (antes se guardaban solo las 20 primeras jugadas).")
    games = core.load_games()
    if not games:
        raise SetupError("No hay partidas en tu archivo de chess.com.")
    o = options(games, time_class, measure=window is None)
    if not window:
        window = ask(o) if interactive and o["ask"] else o["recommended"]
    since = core.window_since(window)
    gs = [g for g in games if g["time_class"] == o["time_class"] and g["end"] >= since]
    if not gs:
        raise SetupError(f"No tienes partidas de {o['time_class']} en esa ventana ({LABELS[window].lower()}). "
                         "Elige una más larga.")
    cfg = dict(core.config.load())
    cfg["analysis"] = {"window": window, "time_class": o["time_class"]}
    core.config.save(cfg)

    start = time.monotonic()
    core.evaluate_games(gs, log=log)
    core.rebuild_report()
    report = core.load_report()
    return {"window": window, "time_class": o["time_class"], "games": len(gs), "since": since,
            "minutes": round((time.monotonic() - start) / 60, 1),
            "puzzles": len(report.get("puzzles", [])), "drills": len(report.get("drills", [])),
            "blunders_per_game": report.get("blunders", {}).get("per_game"),
            "date": date.today().isoformat()}
