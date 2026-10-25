"""Shared data layer for the teachess CLI and dashboard: sync with chess.com, load games, read the moment.

The pipeline scripts at the repo root stay the source of truth; this module calls them in-process
and adds what the live tools need (incremental sync, the current session, single-game review).
"""

from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
import shutil
import statistics
import threading
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import urllib.error

import chess
import chess.engine

from .errors import SetupError, Unreachable

from . import build_report, config, engine_eval, fetch_games, parse_games, paths
from .progress import Bar

DATA = paths.DATA
GAMES = DATA / "games.jsonl"
EVALS = DATA / "evals.json"
REPORT = DATA / "report.json"
PLAYER = DATA / "player"  # whose games this folder holds: one player per folder, never mixed
DEEP = DATA / "deep.json"  # positions re-checked at DEEP_DEPTH: the best moves the player is shown
MADRID = ZoneInfo("Europe/Madrid")
PLIES = 24
DEPTH = 12  # depth the original evals.json was built with
# Depth 12 finds blunders well but can name a different "best move" from run to run when two moves are close;
# every best move shown to the player (a game's blunders, the puzzles) is re-checked here, with a clean hash so
# the answer is the same every time (about a second per position)
DEEP_DEPTH = 18
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


# How much of the history is analysed, chosen per player by `teachess setup` (config.json "analysis")
WINDOWS = {"3m": 91, "6m": 182, "1y": 365, "2y": 730, "3y": 1095, "all": None}
DEFAULT_ANALYSIS = {"window": "1y", "time_class": "rapid"}


def analysis() -> dict:
    a = {**DEFAULT_ANALYSIS, **(config.load().get("analysis") or {})}
    return a if a["window"] in WINDOWS and a["time_class"] in TIME_CLASSES else dict(DEFAULT_ANALYSIS)


def tc() -> str:
    """The time control teachess coaches: the one the player plays most, chosen at setup."""
    return analysis()["time_class"]


def window_since(window: str | None = None) -> str:
    days = WINDOWS[window or analysis()["window"]]
    return "0000-00-00" if days is None else (date.today() - timedelta(days=days)).isoformat()


def key(board: chess.Board) -> str:
    return build_report.key(board)


# --- sync ------------------------------------------------------------------------------------

def sync(user: str | None = None, log=print, analyse: bool = True, since_month: str | None = None,
         show: bool = False) -> list[dict]:
    """Fetch the current month (and any month not cached yet), merge new games, evaluate them move by
    move and rebuild the report (analyse=False stops after the merge). Returns the new games, oldest first.

    since_month ("YYYY-MM") leaves older months alone: setup downloads only the window it needs, and once a window
    is chosen every sync keeps to it (the watcher never pulls years of history nobody analyses). show draws a
    progress bar over the months to download."""
    try:
        user = user or config.chess_user()
    except SystemExit as e:  # config.py is shared with the root scripts, which exit on a missing user
        raise SetupError(str(e.code)) from None
    if since_month is None and config.load().get("analysis"):
        since_month = window_since()[:7]
    with _lock:
        claim_folder(user)
        fetch_games.RAW.mkdir(parents=True, exist_ok=True)
        current = date.today().strftime("%Y-%m")
        try:
            archives = fetch_games.get(f"https://api.chess.com/pub/player/{user}/games/archives")["archives"]
            claim_folder(user, record=True)  # chess.com knows the player: now the folder is theirs
            fresh, due = [], []
            for url in archives:
                month = "-".join(url.rstrip("/").split("/")[-2:])
                out = fetch_games.RAW / f"{month}.json"
                if (out.exists() and month != current) or (since_month and month < since_month):
                    continue
                due.append((url, out))
            bar = Bar("Descargando tus partidas", len(due), "meses") if show and len(due) > 1 else None
            for i, (url, out) in enumerate(due, 1):
                raw = fetch_games.get(url)["games"]
                # Aside and swapped in: setup may be reading the cache while the watcher syncs
                tmp = out.with_name(f"{out.name}.{os.getpid()}.tmp")
                tmp.write_text(json.dumps({"games": raw}))
                tmp.replace(out)
                fresh.extend(raw)
                if bar:
                    bar.update(i)
            if bar:
                bar.close()
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
        if not new and (REPORT.exists() or not analyse):
            return []
        games.extend(new)
        games.sort(key=end_dt)  # by the moment: the ISO text's offset changes with daylight saving time
        with open(GAMES, "w") as f:
            for g in games:
                f.write(json.dumps(g) + "\n")

        new.sort(key=end_dt)
        if analyse:
            since = window_since()
            evaluate_games([g for g in new if g["time_class"] == tc() and g["end"] >= since], log=log)
            rebuild_report()
        return new


MOVE_FIELDS = ("sans", "plies", "my_clocks", "opp_clocks")


def upgrade_games() -> int:
    """Complete the rows that older versions cut at 40 plies with the whole game from the cached archive.

    Run by `teachess setup`, never by sync: it is a one-off for archives made before whole games were kept.
    Only cut rows change: their moves come from the cache, every other field the old row had is kept. A cut
    row that never ends up "complete" is harmless, since a game shorter than its plies is never called
    complete. Returns how many rows were completed."""
    with _lock:
        games = load_games()
        cut = {g["url"] for g in games if len(g["sans"]) < g["plies"]}
        if not cut:
            return 0
        parse_games.USER = parse_games.USER or config.chess_user()
        whole = {}
        for f in sorted(fetch_games.RAW.glob("*.json")):
            for raw in json.loads(f.read_text())["games"]:
                if raw.get("url") in cut:
                    r = parse_games.parse(raw)
                    if r:
                        whole[r["url"]] = r
        if not whole:
            return 0
        merged = []
        for g in games:
            w = whole.get(g["url"])
            merged.append(g if not w else {**w, **{k: v for k, v in g.items()
                                                   if k not in MOVE_FIELDS and v not in (None, "", [])}})
        tmp = GAMES.with_suffix(".tmp")
        with open(tmp, "w") as f:
            for g in merged:
                f.write(json.dumps(g) + "\n")
        tmp.replace(GAMES)
        return len(whole)


def folder_player() -> str | None:
    return (PLAYER.read_text().strip() or None) if PLAYER.exists() else None


def cached_player() -> str | None:
    """For folders from before data/player: the one username in every cached game, newest months first, read only
    until it is clear (a month with one opponent leaves two candidates)."""
    common = None
    for f in sorted(fetch_games.RAW.glob("*.json"), reverse=True):
        for g in json.loads(f.read_text())["games"]:
            pair = {g["white"]["username"].lower(), g["black"]["username"].lower()}
            common = pair if common is None else common & pair
        if common is not None and len(common) <= 1:
            break
    return common.pop() if common and len(common) == 1 else None


def claim_folder(user: str, record: bool = False) -> None:
    """One player per folder, never mixed, whichever way the player was named (config.json or TEACHESS_USER).
    The check and the record happen under the cross-process lock; record only once chess.com knows the player."""
    with data_lock():
        owner = folder_player()
        if not owner and (GAMES.exists() or any(fetch_games.RAW.glob("*.json"))):
            owner = cached_player()
            if owner is None:  # games here, but no single player in all of them: adopt nobody
                raise SetupError(f"No sé de quién son las partidas de {DATA}. Usa otra carpeta de datos para "
                                 f"seguir a un jugador: TEACHESS_HOME=~/teachess-{user} teachess setup")
        if owner and owner != user:
            raise SetupError(f"Tus datos de teachess son de {owner}, no de {user}. Para seguir a {user}, "
                             f"usa otra carpeta de datos: TEACHESS_HOME=~/teachess-{user} teachess setup")
        if record and not folder_player():
            PLAYER.write_text(user + "\n")


def drop_from_position() -> int:
    """Remove rows of thematic or from-position games, which versions before 2026-10-24 kept and which cannot be
    replayed from the usual first move. Run by `teachess setup`. Returns how many rows went."""
    with _lock:
        odd = {raw.get("url") for f in sorted(fetch_games.RAW.glob("*.json"))
               for raw in json.loads(f.read_text())["games"] if not parse_games.standard_start(raw)}
        games = load_games()
        kept = [g for g in games if g["url"] not in odd]
        if len(kept) == len(games):
            return 0
        tmp = GAMES.with_suffix(".tmp")
        with open(tmp, "w") as f:
            for g in kept:
                f.write(json.dumps(g) + "\n")
        tmp.replace(GAMES)
        return len(games) - len(kept)


def rebuild_report(show: bool = False) -> None:
    build_report.SINCE, build_report.TIME_CLASS = window_since(), tc()
    with data_lock(), contextlib.redirect_stdout(io.StringIO()):  # its summary line is for the script, not you
        build_report.main()
    deepen_report(show)


# --- deep check of what the player is shown ---------------------------------------------------

@contextlib.contextmanager
def data_lock():
    """One process at a time writes report.json and deep.json (the background watcher, the dashboard and the
    CLI may all rebuild). Held only around reading and writing those files, never around Stockfish."""
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / "data.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def write_json(path: Path, data) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(path)


def deep_evals(keys: list[str], compute: bool = True) -> dict:
    """Stockfish at DEEP_DEPTH for these positions (keys as in evals.json), cached in data/deep.json.

    One thread and a fresh hash per position (a new `game`), so the same position always gets the same answer.
    compute=False, or no Stockfish, returns only what the cache already has."""
    cache = json.loads(DEEP.read_text()) if DEEP.exists() else {}
    todo = [k for k in dict.fromkeys(keys) if k not in cache]
    path = shutil.which("stockfish")
    if todo and compute and path:
        fresh = {}
        engine = chess.engine.SimpleEngine.popen_uci(path)
        try:
            engine.configure({"Threads": 1, "Hash": 64})
            for k in todo:
                board = chess.Board(k + " 0 1")
                if board.is_game_over():
                    outcome = board.outcome()
                    fresh[k] = {"cp": 0 if outcome.winner is None else (1000 if outcome.winner else -1000), "best": None}
                    continue
                info = engine.analyse(board, chess.engine.Limit(depth=DEEP_DEPTH), game=object())
                fresh[k] = {"cp": max(-1000, min(1000, info["score"].white().score(mate_score=1000))),
                            "best": board.san(info["pv"][0]) if info.get("pv") else None}
        finally:
            engine.quit()
        with data_lock():  # merge into the file as it is now, another process may have added entries
            cache = json.loads(DEEP.read_text()) if DEEP.exists() else {}
            cache.update(fresh)
            write_json(DEEP, cache)
    return {k: cache[k] for k in keys if k in cache}


def is_blunder(before: int, after: int) -> bool:
    return build_report.win_chances(before) - build_report.win_chances(after) >= build_report.BLUNDER_DROP


def is_mistake(before: int, after: int) -> bool:
    return before - after >= MISTAKE_CP


def confirm(color: str, items: list[dict], counts, compute: bool = True) -> list[dict]:
    """Re-check moves at DEEP_DEPTH. Each item has the position before the move (`fen`, as in the report) and
    the move `played`; `counts(before, after)` says whether the move still counts at that depth (player-side
    centipawns). Kept items get the deep best move, evaluations, `loss` and `drop`; an item whose positions
    are not checked (no Stockfish, or compute=False and not cached yet) is kept as it was."""
    sign = 1 if color == "white" else -1
    pairs = []
    for item in items:
        board = chess.Board(item["fen"])
        board.push_san(item["played"])
        pairs.append((item, item["fen"].rsplit(" ", 2)[0], key(board)))
    deep = deep_evals([k for _, before, after in pairs for k in (before, after)], compute=compute)
    out = []
    for item, before, after in pairs:
        if before not in deep or after not in deep:
            out.append(item)
            continue
        cb, ca = sign * deep[before]["cp"], sign * deep[after]["cp"]
        best = deep[before]["best"]
        if counts(cb, ca) and best and best != item["played"]:
            drop = build_report.win_chances(cb) - build_report.win_chances(ca)
            out.append({**item, "best": best, "before": cb, "after": ca, "loss": cb - ca, "drop": round(drop, 2),
                        "deep": True})
    return out


def confirm_blunders(color: str, found: list[dict], compute: bool = True) -> list[dict]:
    """Blunders (as build_report.blunders returns them) that still throw away BLUNDER_DROP of the winning
    chances at DEEP_DEPTH, with the deep best move."""
    return confirm(color, found, is_blunder, compute)


def deepen_report(show: bool = False) -> None:
    """Re-check what the report shows as right answers at DEEP_DEPTH: the puzzles (still a blunder?) and the
    opening cards (still a mistake of MISTAKE_CP?). Each takes the deep best move; what no longer counts at that
    depth is dropped. Stockfish runs first, outside the lock; then the report as it is now (maybe newer) is
    rewritten from the cache."""
    groups = (("puzzles", is_blunder), ("drills", is_mistake))
    item = lambda c: {"fen": c["fen"], "played": c["played"], "best": c["best"]}
    report = load_report()
    items = [(c, counts) for name, counts in groups for c in report.get(name, [])]
    bar = Bar("Comprobando a fondo las jugadas buenas", len(items)) if show and items else None
    for i, (c, counts) in enumerate(items, 1):
        confirm(c["color"], [item(c)], counts)
        if bar:
            bar.update(i)
    if bar:
        bar.close()
    with data_lock():
        report = load_report()
        if not report:
            return
        for name, counts in groups:
            kept = []
            for c in report.get(name, []):
                checked = confirm(c["color"], [item(c)], counts, compute=False)
                if checked:
                    d = checked[0]
                    if d.get("deep"):
                        c = {**c, "best": d["best"], "eval_best": d["before"], "loss": d["loss"], "drop": d["drop"],
                             "deep": True}
                    kept.append(c)
            report[name] = kept
        write_json(REPORT, report)


def positions(games: list[dict], plies: int | None = None) -> set[str]:
    """Every position of these games (the first `plies` moves, or all of them)."""
    out = set()
    for g in games:
        board = chess.Board()
        out.add(key(board))
        for san in g["sans"][:plies]:
            board.push_san(san)
            out.add(key(board))
    return out


def evaluate_games(games: list[dict], plies: int | None = None, log=print) -> None:
    """Add Stockfish evaluations for every position of these games (or their first plies) that is missing."""
    evals = load_evals()
    todo = sorted(positions(games, plies) - evals.keys())
    if not todo:
        return
    if len(todo) > BULK:
        # First run or a long break: the parallel batch evaluator is much faster.
        n_games, n_positions = (f"{n:,}".replace(",", ".") for n in (len(games), len(todo)))
        log(f"Stockfish revisa cada jugada de {n_games} partidas ({n_positions} posiciones), "
            f"calculando unas {DEPTH} jugadas por delante en cada una.")
        engine_eval.SINCE, engine_eval.PLIES, engine_eval.DEPTH = window_since(), plies, DEPTH
        engine_eval.TIME_CLASS = tc()
        engine_eval.main()
        return
    path = shutil.which("stockfish")
    if not path:
        log("Stockfish no está instalado (brew install stockfish): sin evaluaciones nuevas.")
        return
    log(f"Stockfish: evaluando {len(todo)} posiciones…")
    bar = Bar("Analizando", len(todo)) if len(todo) >= 200 else None
    engine = chess.engine.SimpleEngine.popen_uci(path)
    try:
        engine.configure({"Threads": 2, "Hash": 64})
        for i, fen in enumerate(todo, 1):
            if bar:
                bar.update(i)
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
        if bar:
            bar.close()
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


def eval_text(cp: int) -> str:
    """A player-side evaluation in words or pawns: 'ganada', '+1,2', '−3,0', 'perdida'."""
    if cp >= 900:
        return "ganada"
    if cp <= -900:
        return "perdida"
    return ("+" if cp >= 0 else "−") + pawns(cp)


def review(g: dict, evals: dict | None = None, report: dict | None = None, deep: bool = False) -> dict:
    """The player's costly moves: opening mistakes (first 12 moves, ≥ 0.8 pawns) and blunders anywhere
    (moves that threw away a big part of the winning chances), with a one-line verdict.

    deep=True re-checks the blunders at DEEP_DEPTH (about a second each), for what the player is shown;
    deep="cached" uses only checks already in the cache (for the dashboard, which must answer at once);
    statistics over many games leave it off."""
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
                "ply": i, "fen": before + " 0 1", "move": move_label(i) + san, "played": san, "best": evals[before]["best"],
                "best_move": move_label(i) + (evals[before]["best"] or "?"),
                "loss": loss, "line": format_line(g["sans"][:i]),
                "repeated": d["times"] if d else None, "reached": d["reached"] if d else None,
            })
    found, complete = build_report.blunders(g, evals)
    if deep:
        found = confirm_blunders(g["color"], found, compute=deep != "cached")
        mistakes = [{**m, "best_move": move_label(m["ply"]) + m["best"]} if m.get("deep") else m
                    for m in confirm(g["color"], mistakes, is_mistake, compute=deep != "cached")]
    blunders = [{"ply": b["ply"], "move": move_label(b["ply"]) + b["played"], "played": b["played"],
                 "best": b["best"], "best_move": move_label(b["ply"]) + b["best"], "before": b["before"],
                 "after": b["after"], "loss": b["loss"], "drop": b["drop"]} for b in found]
    first = mistakes[0] if mistakes else None
    worst = max(blunders, key=lambda b: b["drop"]) if blunders else None

    parts = []
    if worst:
        parts.append(f"Error grave en la jugada {worst['move']}: pasas de {eval_text(worst['before'])} a "
                     f"{eval_text(worst['after'])}. La buena era {worst['best_move']}.")
    if first and first["repeated"]:
        parts.append(f"En la apertura, {first['move']} otra vez: el mismo error que en {first['repeated']} partidas."
                     if worst else f"Error en la jugada {first['move']}: el mismo que en {first['repeated']} "
                     f"partidas. La buena era {first['best_move']}.")
    elif first and not worst:
        parts.append(f"Primer error serio en la jugada {first['move']} (pierde {pawns(first['loss'])} peones). "
                     f"La buena era {first['best_move']}.")
    if not parts:
        final = key(board_after(g))
        if missing:
            parts.append("Sin evaluación de Stockfish para esta partida.")
        elif complete and g["score"] == 0 and final in evals:
            # Lost without one big blunder: how it ended says more than "no mistakes"
            how = HOW_LOSS.get(g["how"], g["how"])
            parts.append(f"Sin errores graves: {how} en la jugada {(g['plies'] + 1) // 2} con la partida "
                         f"{eval_text(sign * evals[final]['cp'])} según Stockfish.")
        elif complete:
            parts.append("Sin errores graves en toda la partida.")
        else:
            parts.append(f"Ningún error serio en las primeras {PLIES // 2} jugadas.")
    return {"url": g["url"], "mistakes": mistakes, "first": first, "blunders": blunders, "worst": worst,
            "fully_evaluated": complete, "verdict": " ".join(parts), "unevaluated_moves": missing}


def board_after(g: dict) -> chess.Board:
    board = chess.Board()
    for san in g["sans"]:
        board.push_san(san)
    return board


def pawns(cp: int) -> str:
    """Centipawns as Spanish-formatted pawns: 137 -> '1,4'."""
    return f"{abs(cp) / 100:.1f}".replace(".", ",")


def find_game(games: list[dict], ref: str) -> dict | None:
    if ref in ("last", "ultima", "última"):
        rapid = [g for g in games if g["time_class"] == tc()]
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
    rapid = [g for g in games if g["time_class"] == tc() and end_dt(g) <= at]
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


def move_num(x: float) -> str:
    """A median move number the Spanish way: 7 -> '7', 6.5 -> '6,5'."""
    return f"{x:g}".replace(".", ",")


def fmt_pct(x) -> str:
    return "—" if x is None else f"{x:.1f} %".replace(".", ",")


# --- insights for the dashboard ----------------------------------------------------------------

def rating_now(games: list[dict]) -> dict:
    rapid = [g for g in games if g["time_class"] == tc()]
    if not rapid:
        return {"now": None, "best": None, "best_date": None}
    best = max(rapid, key=lambda g: g["my_rating"])
    d = end_dt(best)
    return {"now": rapid[-1]["my_rating"], "best": best["my_rating"],
            "best_date": f"{d.day} {MONTHS[d.month - 1]} {d.year}"}


def monthly_rating(games: list[dict], months: int = 14) -> list[dict]:
    last = {}
    for g in games:
        if g["time_class"] == tc():
            last[g["end"][:7]] = g["my_rating"]
    keys = sorted(last)[-months:]
    return [{"month": k, "label": f"{MONTHS[int(k[5:]) - 1]} {k[2:4]}", "rating": last[k]} for k in keys]


def heatmap(games: list[dict], since: str) -> list[dict]:
    cells = defaultdict(lambda: [0, 0.0])
    for g in games:
        if g["time_class"] != tc() or g["end"] < since:
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
        if g["time_class"] != tc() or g["end"] < since or not g["opening"]:
            continue
        fam = build_report.family(g["opening"])
        for n in range(1, min(depth, len(g["sans"])) + 1):
            counts[g["color"]][" ".join(g["sans"][:n])][fam] += 1
    return {c: {line: cnt.most_common(1)[0][0] for line, cnt in lines.items()} for c, lines in counts.items()}


def progress(games: list[dict] | None = None, report: dict | None = None, months: int = 6) -> dict:
    """Month by month in the coached time control: games, points, rating, how early the first serious
    mistake comes and how many blunders a game has.

    The yearly figures from the report are the baseline each month is compared against."""
    games = load_games() if games is None else games
    report = load_report() if report is None else report
    evals = load_evals()
    since = window_since()
    by_month = defaultdict(list)
    for g in games:
        # Only the analysed window: older games have no evaluations past their shared opening positions
        if g["time_class"] == tc() and g["end"] >= since:
            by_month[g["end"][:7]].append(g)
    rows = []
    for k in sorted(by_month)[-months:]:
        gs = by_month[k]
        evaluated, firsts, per_game = 0, [], []
        for g in gs:
            r = review(g, evals, report)
            if r["first"]:
                evaluated += 1
                firsts.append(r["first"]["ply"] // 2 + 1)
            elif not r["unevaluated_moves"]:
                evaluated += 1
            if r["fully_evaluated"]:
                per_game.append(len(r["blunders"]))
        rows.append({
            "month": k, "label": f"{MONTHS[int(k[5:]) - 1]} {k[2:4]}", "games": len(gs),
            "score": round(100 * sum(g["score"] for g in gs) / len(gs), 1), "rating": gs[-1]["my_rating"],
            "evaluated": evaluated,
            "mistake_by12": round(100 * len(firsts) / evaluated) if evaluated else None,
            "median_first_mistake": float(statistics.median(firsts)) if firsts else None,
            "blunders_per_game": round(statistics.mean(per_game), 2) if per_game else None,
        })
    totals, fm = report.get("totals", {}), report.get("first_mistake", {})
    after = report.get("habits", {}).get("after", {})
    return {
        "rating": rating_now(games), "months": rows,
        "year": {"since": report.get("since"), "games": totals.get("recent"), "score": totals.get("recent_score"),
                 "mistake_by12": fm.get("share_by_move12"), "median_first_mistake": fm.get("median_move"),
                 "score_after_loss": after.get("tras perder", {}).get("score"),
                 "score_after_win": after.get("tras ganar", {}).get("score"),
                 "blunders_per_game": report.get("blunders", {}).get("per_game")},
    }
