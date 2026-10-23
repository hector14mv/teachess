"""Shared data layer for the teachess CLI and dashboard: sync with chess.com, load games, read the moment.

The pipeline scripts at the repo root stay the source of truth; this module calls them in-process
and adds what the live tools need (incremental sync, the current session, single-game review).
"""

from __future__ import annotations

import json
import shutil
import statistics
import sys
import threading
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import urllib.error

import chess
import chess.engine

from .errors import SetupError, Unreachable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_report  # noqa: E402
import config  # noqa: E402
import engine_eval  # noqa: E402
import fetch_games  # noqa: E402
import parse_games  # noqa: E402

DATA = ROOT / "data"
GAMES = DATA / "games.jsonl"
EVALS = DATA / "evals.json"
REPORT = DATA / "report.json"
MADRID = ZoneInfo("Europe/Madrid")
PLIES = 24
DEPTH = 12  # depth the original evals.json was built with
BULK = 3000  # above this many positions, evaluate with the parallel batch script
MISTAKE_CP = build_report.MISTAKE_CP
SESSION_GAP_MIN = build_report.SESSION_GAP_MIN
TIME_CLASSES = ("rapid", "blitz", "bullet", "daily")

MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
SIDE = {"white": "blancas", "black": "negras"}
BAND_PHRASE = {"madrugada": "De madrugada", "mañana": "Por la mañana", "tarde": "Por la tarde", "noche": "Por la noche"}
HOW_WIN = {"resigned": "se rindió", "checkmated": "mate", "timeout": "por tiempo",
           "abandoned": "abandonó", "timevsinsufficient": "por tiempo"}
HOW_LOSS = {"resigned": "te rendiste", "checkmated": "te dieron mate", "timeout": "por tiempo",
            "abandoned": "abandonaste"}

_lock = threading.RLock()


# --- loading ---------------------------------------------------------------------------------

def load_games() -> list[dict]:
    if not GAMES.exists():
        return []
    with open(GAMES) as f:
        return [json.loads(line) for line in f]


def load_evals() -> dict:
    return json.loads(EVALS.read_text()) if EVALS.exists() else {}


def load_report() -> dict:
    return json.loads(REPORT.read_text()) if REPORT.exists() else {}


def end_dt(g: dict) -> datetime:
    return datetime.fromisoformat(g["end"])


def now() -> datetime:
    return datetime.now(MADRID)


def since_one_year() -> str:
    return (date.today() - timedelta(days=365)).isoformat()


def key(board: chess.Board) -> str:
    return build_report.key(board)


# --- sync ------------------------------------------------------------------------------------

def sync(user: str | None = None, log=print) -> list[dict]:
    """Fetch the current month (and any month not cached yet), merge new games, evaluate their
    openings and rebuild the report. Returns the newly seen games, oldest first."""
    try:
        user = user or config.chess_user()
    except SystemExit as e:  # config.py is shared with the root scripts, which exit on a missing user
        raise SetupError(str(e.code)) from None
    with _lock:
        fetch_games.RAW.mkdir(parents=True, exist_ok=True)
        current = date.today().strftime("%Y-%m")
        try:
            archives = fetch_games.get(f"https://api.chess.com/pub/player/{user}/games/archives")["archives"]
            fresh = []
            for url in archives:
                month = "-".join(url.rstrip("/").split("/")[-2:])
                out = fetch_games.RAW / f"{month}.json"
                if out.exists() and month != current:
                    continue
                raw = fetch_games.get(url)["games"]
                out.write_text(json.dumps({"games": raw}))
                fresh.extend(raw)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise SetupError(f"chess.com no conoce al jugador «{user}».") from None
            raise Unreachable(f"chess.com respondió {e.code}.") from None
        except (urllib.error.URLError, TimeoutError, RuntimeError) as e:
            raise Unreachable(f"No llego a chess.com: {e}") from None

        parse_games.USER = user
        games = load_games()
        known = {g["url"] for g in games}
        new = [r for r in (parse_games.parse(g) for g in fresh) if r and r["url"] not in known]
        if not new and REPORT.exists():
            return []
        games.extend(new)
        games.sort(key=lambda g: g["end"])
        with open(GAMES, "w") as f:
            for g in games:
                f.write(json.dumps(g) + "\n")

        since = since_one_year()
        evaluate_games([g for g in new if g["time_class"] == "rapid" and g["end"] >= since], log=log)
        build_report.SINCE = since
        build_report.main()
        new.sort(key=lambda g: g["end"])
        return new


def evaluate_games(games: list[dict], plies: int = PLIES, log=print) -> None:
    """Add Stockfish evaluations for every opening position of these games that is missing."""
    evals = load_evals()
    todo = []
    for g in games:
        board = chess.Board()
        keys = [key(board)]
        for san in g["sans"][:plies]:
            board.push_san(san)
            keys.append(key(board))
        todo.extend(k for k in keys if k not in evals and k not in todo)
    if not todo:
        return
    if len(todo) > BULK:
        # First run or a long break: the parallel batch evaluator is much faster.
        log(f"Stockfish: evaluando {len(todo)} posiciones en paralelo (la primera vez tarda un rato)…")
        engine_eval.SINCE, engine_eval.PLIES, engine_eval.DEPTH = since_one_year(), plies, DEPTH
        engine_eval.main()
        return
    path = shutil.which("stockfish")
    if not path:
        log("Stockfish no está instalado (brew install stockfish): sin evaluaciones nuevas.")
        return
    log(f"Stockfish: evaluando {len(todo)} posiciones…")
    engine = chess.engine.SimpleEngine.popen_uci(path)
    try:
        engine.configure({"Threads": 2, "Hash": 64})
        for fen in todo:
            board = chess.Board(fen + " 0 1")
            if board.is_game_over():
                outcome = board.outcome()
                evals[fen] = {"cp": 0 if outcome.winner is None else (1000 if outcome.winner else -1000),
                              "best": None}
                continue
            info = engine.analyse(board, chess.engine.Limit(depth=DEPTH))
            cp = max(-1000, min(1000, info["score"].white().score(mate_score=1000)))
            evals[fen] = {"cp": cp, "best": board.san(info["pv"][0]) if info.get("pv") else None}
    finally:
        engine.quit()
    EVALS.write_text(json.dumps(evals))


# --- one game ---------------------------------------------------------------------------------

def move_label(ply: int) -> str:
    """'6.' for White's 6th move, '6...' for Black's."""
    return f"{ply // 2 + 1}." if ply % 2 == 0 else f"{ply // 2 + 1}..."


def format_line(sans: list[str]) -> str:
    out = []
    for i, s in enumerate(sans):
        out.append(f"{i // 2 + 1}.{s}" if i % 2 == 0 else s)
    return " ".join(out)


def review(g: dict, evals: dict | None = None, report: dict | None = None) -> dict:
    """Walk the game's opening through the engine and list the player's costly moves."""
    evals = load_evals() if evals is None else evals
    report = load_report() if report is None else report
    drills = {(d["fen"].rsplit(" ", 2)[0], d["played"]): d for d in report.get("drills", [])}
    sign = 1 if g["color"] == "white" else -1
    mine = 0 if g["color"] == "white" else 1
    board = chess.Board()
    mistakes, missing = [], 0
    for i, san in enumerate(g["sans"][:PLIES]):
        before = key(board)
        board.push_san(san)
        after = key(board)
        if i % 2 != mine:
            continue
        if before not in evals or after not in evals:
            missing += 1
            continue
        loss = sign * (evals[before]["cp"] - evals[after]["cp"])
        if loss >= MISTAKE_CP:
            d = drills.get((before, san))
            mistakes.append({
                "ply": i, "move": move_label(i) + san, "played": san, "best": evals[before]["best"],
                "best_move": move_label(i) + (evals[before]["best"] or "?"),
                "loss": loss, "line": format_line(g["sans"][:i]),
                "repeated": d["times"] if d else None, "reached": d["reached"] if d else None,
            })
    first = mistakes[0] if mistakes else None
    if first and first["repeated"]:
        verdict = (f"Error en la jugada {first['move']}: el mismo que en {first['repeated']} partidas. "
                   f"La buena era {first['best_move']}.")
    elif first:
        verdict = (f"Primer error serio en la jugada {first['move']} (pierde {pawns(first['loss'])} peones). "
                   f"La buena era {first['best_move']}.")
    elif missing:
        verdict = "Sin evaluación de Stockfish para esta apertura."
    else:
        verdict = f"Ningún error serio en las primeras {PLIES // 2} jugadas."
    return {"url": g["url"], "mistakes": mistakes, "first": first, "verdict": verdict, "unevaluated_moves": missing}


def pawns(cp: int) -> str:
    """Centipawns as Spanish-formatted pawns: 137 -> '1,4'."""
    return f"{abs(cp) / 100:.1f}".replace(".", ",")


def find_game(games: list[dict], ref: str) -> dict | None:
    if ref in ("last", "ultima", "última"):
        rapid = [g for g in games if g["time_class"] == "rapid"]
        return rapid[-1] if rapid else None
    ref = ref.rstrip("/")
    for g in reversed(games):
        if g["url"].rstrip("/").endswith(ref.rsplit("/", 1)[-1]):
            return g
    return None


# --- the moment -------------------------------------------------------------------------------

def band(hour: int) -> str:
    return "madrugada" if hour < 8 else "mañana" if hour < 14 else "tarde" if hour < 20 else "noche"


def describe(g: dict) -> dict:
    """A game as the dashboard and the CLI list it."""
    d = end_dt(g)
    if g["score"] == 1:
        result, how = "Ganada", HOW_WIN.get(g["how"], g["how"])
    elif g["score"] == 0:
        result, how = "Perdida", HOW_LOSS.get(g["how"], g["how"])
    else:
        result, how = "Tablas", "tablas"
    if g["how"] == "abandoned" and g["score"] == 0 and g["plies"] <= 12:
        how = f"abandonaste en la jugada {g['plies'] // 2 + 1}"
    return {
        "url": g["url"], "end": g["end"], "time_class": g["time_class"],
        "when": f"{d.day} {MONTHS[d.month - 1]} {d:%H:%M}",
        "result": result, "score": g["score"], "side": SIDE[g["color"]], "color": g["color"],
        "opening": build_report.family(g["opening"]) if g["opening"] else "Sin apertura",
        "how": how, "opp": g["opp"], "opp_rating": g["opp_rating"], "my_rating": g["my_rating"],
        "acc": round(g["my_acc"]) if g.get("my_acc") else None, "plies": g["plies"],
    }


def today(games: list[dict] | None = None, report: dict | None = None, at: datetime | None = None) -> dict:
    """Where the player stands right now: current session, losing streak and what the history says."""
    games = load_games() if games is None else games
    report = load_report() if report is None else report
    at = at or now()
    rapid = [g for g in games if g["time_class"] == "rapid" and end_dt(g) <= at]
    habits = report.get("habits", {})
    after = habits.get("after", {})
    bands = habits.get("band", {})
    after_loss = after.get("tras perder", {}).get("score")
    after_win = after.get("tras ganar", {}).get("score")

    session = []
    if rapid and (at - end_dt(rapid[-1])).total_seconds() / 60 < SESSION_GAP_MIN:
        session = [rapid[-1]]
        for p in reversed(rapid[:-1]):
            if (end_dt(session[0]) - end_dt(p)).total_seconds() / 60 >= SESSION_GAP_MIN:
                break
            session.insert(0, p)
    streak = 0
    for g in reversed(session):
        if g["score"] != 0:
            break
        streak += 1

    now_band = band(at.hour)
    last = describe(rapid[-1]) if rapid else None
    if streak >= 2:
        state = "streak"
        advice = f"Para {SESSION_GAP_MIN} minutos: después de perder sacas el {fmt_pct(after_loss)} de los puntos."
    elif streak == 1:
        state = "loss"
        advice = f"Respira antes de la siguiente: después de perder sacas el {fmt_pct(after_loss)} de los puntos."
    elif session:
        state = "win" if session[-1]["score"] == 1 else "draw"
        advice = f"Buen momento: después de ganar sacas el {fmt_pct(after_win)} de los puntos."
    elif not rapid:
        state = "empty"
        advice = "Aún no hay partidas: ejecuta teachess sync (la primera vez tarda unos minutos)."
    else:
        state = "idle"
        advice = f"{BAND_PHRASE[now_band]} sacas el {fmt_pct(bands.get(now_band, {}).get('score'))} de los puntos."
    return {
        "at": at.isoformat(), "state": state, "in_session": bool(session),
        "session_games": len(session), "session_points": sum(g["score"] for g in session),
        "losses_in_a_row": streak, "score_after_loss": after_loss, "score_after_win": after_win,
        "band": now_band, "band_score": bands.get(now_band, {}).get("score"),
        "advice": advice, "last": last,
        "minutes_since_last": round((at - end_dt(rapid[-1])).total_seconds() / 60) if rapid else None,
    }


def fmt_pct(x) -> str:
    return "—" if x is None else f"{x:.1f} %".replace(".", ",")


# --- insights for the dashboard ----------------------------------------------------------------

def rating_now(games: list[dict]) -> dict:
    rapid = [g for g in games if g["time_class"] == "rapid"]
    if not rapid:
        return {"now": None, "best": None, "best_date": None}
    best = max(rapid, key=lambda g: g["my_rating"])
    d = end_dt(best)
    return {"now": rapid[-1]["my_rating"], "best": best["my_rating"],
            "best_date": f"{d.day} {MONTHS[d.month - 1]} {d.year}"}


def monthly_rating(games: list[dict], months: int = 14) -> list[dict]:
    last = {}
    for g in games:
        if g["time_class"] == "rapid":
            last[g["end"][:7]] = g["my_rating"]
    keys = sorted(last)[-months:]
    return [{"month": k, "label": f"{MONTHS[int(k[5:]) - 1]} {k[2:4]}", "rating": last[k]} for k in keys]


def heatmap(games: list[dict], since: str) -> list[dict]:
    cells = defaultdict(lambda: [0, 0.0])
    for g in games:
        if g["time_class"] != "rapid" or g["end"] < since:
            continue
        d = end_dt(g)
        c = cells[(d.weekday(), d.hour // 3)]
        c[0] += 1
        c[1] += g["score"]
    return [{"day": DAYS[wd], "cells": [
        {"n": cells[(wd, b)][0],
         "score": round(100 * cells[(wd, b)][1] / cells[(wd, b)][0]) if cells[(wd, b)][0] else None}
        for b in range(8)]} for wd in range(7)]


def branch_names(games: list[dict], since: str, depth: int = 10) -> dict:
    """Most common opening family of the games that went through each line, per colour."""
    counts = {"white": defaultdict(Counter), "black": defaultdict(Counter)}
    for g in games:
        if g["time_class"] != "rapid" or g["end"] < since or not g["opening"]:
            continue
        fam = build_report.family(g["opening"])
        for n in range(1, min(depth, len(g["sans"])) + 1):
            counts[g["color"]][" ".join(g["sans"][:n])][fam] += 1
    return {c: {line: cnt.most_common(1)[0][0] for line, cnt in lines.items()} for c, lines in counts.items()}


def progress(games: list[dict] | None = None, report: dict | None = None, months: int = 6) -> dict:
    """Month by month in rapid: games, points, rating, and how early the first serious mistake comes.

    The yearly figures from the report are the baseline each month is compared against."""
    games = load_games() if games is None else games
    report = load_report() if report is None else report
    evals = load_evals()
    by_month = defaultdict(list)
    for g in games:
        if g["time_class"] == "rapid":
            by_month[g["end"][:7]].append(g)
    rows = []
    for k in sorted(by_month)[-months:]:
        gs = by_month[k]
        evaluated, firsts = 0, []
        for g in gs:
            r = review(g, evals, report)
            if r["first"]:
                evaluated += 1
                firsts.append(r["first"]["ply"] // 2 + 1)
            elif not r["unevaluated_moves"]:
                evaluated += 1
        rows.append({
            "month": k, "label": f"{MONTHS[int(k[5:]) - 1]} {k[2:4]}", "games": len(gs),
            "score": round(100 * sum(g["score"] for g in gs) / len(gs), 1), "rating": gs[-1]["my_rating"],
            "evaluated": evaluated,
            "mistake_by12": round(100 * len(firsts) / evaluated) if evaluated else None,
            "median_first_mistake": float(statistics.median(firsts)) if firsts else None,
        })
    totals, fm = report.get("totals", {}), report.get("first_mistake", {})
    after = report.get("habits", {}).get("after", {})
    return {
        "rating": rating_now(games), "months": rows,
        "year": {"since": report.get("since"), "games": totals.get("recent"), "score": totals.get("recent_score"),
                 "mistake_by12": fm.get("share_by_move12"), "median_first_mistake": fm.get("median_move"),
                 "score_after_loss": after.get("tras perder", {}).get("score"),
                 "score_after_win": after.get("tras ganar", {}).get("score")},
    }
