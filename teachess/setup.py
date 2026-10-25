"""teachess setup: choose, for this player, how much of the history to analyse, then analyse it move by move.

The options come from the player's own archive: the time control they play most, how many games each window
holds, and how long Stockfish would take on this machine (measured on a sample, not guessed). The recommended
window is the shortest one with enough games to find patterns, because recent games say how you play now.

The options come before the download: chess.com's stats give the all-time totals at once, the newest months are
downloaded until a window holds enough games to recommend (a few seconds), and an older window is estimated from
its months' sizes (HEAD requests, no download) and marked "~". Only the window the player picks is then
downloaded.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import urllib.error
from collections import Counter
from datetime import date

import chess
import chess.engine

from . import core
from .errors import SetupError, Unreachable, UsageError

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


def month(window: str) -> str:
    """The first month ("YYYY-MM") a window reaches."""
    return core.window_since(window)[:7]


def chesscom(url: str, head: bool = False):
    try:
        return core.fetch_games.size(url) if head else core.fetch_games.get(url)
    except urllib.error.HTTPError as e:
        raise Unreachable(f"chess.com respondió {e.code}.") from None
    except (urllib.error.URLError, TimeoutError, RuntimeError) as e:
        raise Unreachable(f"No llego a chess.com: {e}") from None


def survey(user: str) -> dict:
    """What chess.com says before any download: the months with games, and all-time totals per time control
    (None when its stats do not answer: then the whole archive is downloaded, as before)."""
    base = f"https://api.chess.com/pub/player/{user}"
    archives = chesscom(f"{base}/games/archives")["archives"]
    try:
        stats = core.fetch_games.get(f"{base}/stats")
        totals = {t: sum((stats.get(f"chess_{t}") or {}).get("record", {}).get(k, 0) for k in ("win", "loss", "draw"))
                  for t in core.TIME_CLASSES}
    except Exception:  # an optional shortcut: without it, setup downloads everything
        totals = None
    months = {"-".join(u.rstrip("/").split("/")[-2:]): u for u in archives}
    return {"months": months, "totals": totals}


def covered(s: dict) -> str:
    """The oldest month from which every month of the archive is downloaded ("9999-99": none, "0000-00": all)."""
    cached = {f.stem for f in core.fetch_games.RAW.glob("*.json")} if core.fetch_games.RAW.exists() else set()
    oldest = "9999-99"
    for m in sorted(s["months"], reverse=True):
        if m not in cached:
            return oldest
        oldest = m
    return "0000-00"  # the whole archive


def raw_counts(months: list[str]) -> Counter:
    """Games per time control in these downloaded months, as chess.com counts them (every game, standard or not)."""
    out = Counter()
    for m in months:
        f = core.fetch_games.RAW / f"{m}.json"
        if f.exists():
            out.update(g.get("time_class") for g in json.loads(f.read_text())["games"])
    return out


def estimate(s: dict, tc: str, since: str, cover: str) -> int:
    """Games of `tc` from month `since` up to the downloaded part (which starts at `cover`), not downloaded:
    each month's size (HEAD) over the bytes a game takes in downloaded months, times the share `tc` has in
    the rest of the archive (chess.com's totals minus what is downloaded)."""
    done = sorted(m for m in s["months"] if m >= cover)
    sample = done[-3:]
    sizes = sum(chesscom(s["months"][m], head=True) for m in sample)
    per_game = sizes / max(1, sum(raw_counts(sample).values()))
    have = raw_counts(done)
    rest_all = sum(s["totals"].values()) - sum(have[t] for t in core.TIME_CLASSES)
    share = (s["totals"][tc] - have[tc]) / rest_all if rest_all > 0 else 0
    older = [m for m in s["months"] if since <= m < cover]
    return round(sum(chesscom(s["months"][m], head=True) for m in older) / max(1, per_game) * share)


def options(games: list[dict], time_class: str | None = None, measure: bool = True, s: dict | None = None) -> dict:
    """The windows worth offering this player, with games, positions left and minutes for each. With a survey
    `s`, a window older than what is downloaded comes from chess.com's totals or an estimate ("estimated")."""
    year_ago = core.window_since("1y")
    counts = {t: sum(1 for g in games if g["time_class"] == t) for t in core.TIME_CLASSES}
    if s and s["totals"]:
        counts = dict(s["totals"])
    recent = {t: sum(1 for g in games if g["time_class"] == t and g["end"] >= year_ago) for t in core.TIME_CLASSES}
    tc = time_class or max(core.TIME_CLASSES, key=lambda t: (recent[t], counts[t]))
    if time_class and time_class not in core.TIME_CLASSES:
        raise UsageError(f"Ritmo desconocido: {time_class}.")
    mine = [g for g in games if g["time_class"] == tc]
    if not mine:
        raise SetupError(f"No hay partidas de {tc} en tu archivo de chess.com.")
    evals = core.load_evals()
    first = min(s["months"]) if s and s["months"] else core.end_dt(mine[0]).strftime("%Y-%m")
    cover = covered(s) if s and s["totals"] else "0000-00"
    opts, last_n, per_game = [], None, 60.0  # distinct positions a game brings, measured below
    for w in OFFERED:
        since = month(w)
        if since >= cover:
            gs = [g for g in mine if g["end"] >= core.window_since(w)]
            pos = core.positions(gs)
            n, todo, guess = len(gs), sorted(pos - evals.keys()), False
            per_game = len(pos) / max(1, n)
        elif since <= first:  # the whole archive: chess.com's own total
            n, todo, guess = s["totals"][tc], None, False
        else:
            n = sum(1 for g in mine if g["end"] >= core.window_since(w)) + estimate(s, tc, since, cover)
            todo, guess = None, True
        if not n or n == last_n:
            continue  # nothing there, or the account is younger than this window: same games as the last one
        last_n = n
        opts.append({"window": w, "label": LABELS[w] + (f" (desde {first[:4]})" if w == "all" else ""),
                     "games": n, "positions": None if todo is None else len(todo), "todo": todo,
                     "estimated": guess})
    # The batch runs on every core but one, yet scales to about half of them (efficiency cores, memory):
    # a clean install measured 4 minutes where a linear estimate said 2
    workers = max(1, ((os.cpu_count() or 2) - 1) / 2)
    known = [o for o in opts if o["todo"] is not None]
    pace = per_position(known[-1]["todo"]) if measure and known else None
    # A window not downloaded yet: none of its games is evaluated; each brings the positions a game brings in
    # the largest known window (counted whole, evaluated or not)
    for o in opts:
        todo = o.pop("todo")
        if o["positions"] is None:
            o["positions"] = known[-1]["positions"] + round((o["games"] - known[-1]["games"]) * per_game) \
                if known else round(o["games"] * per_game)
        parallel = workers if o["positions"] > core.BULK else 1
        o["minutes"] = None if pace is None else max(1, round(o["positions"] * pace / parallel / 60))
    rec = next((o["window"] for o in opts if o["games"] >= MIN_GAMES), opts[-1]["window"])
    return {"time_class": tc, "games_by_time_class": counts, "options": opts, "recommended": rec,
            "ask": len(opts) > 1 and opts[-1]["games"] > MIN_GAMES}


def ask(o: dict) -> str:
    n = f"{sum(o['games_by_time_class'].values()):,}".replace(",", ".")
    others = ", ".join(f"{v} de {t}" for t, v in o["games_by_time_class"].items() if v and t != o["time_class"])
    print(f"\nTienes {n} partidas en chess.com; analizo las de {o['time_class']}" + (f" (también hay {others})." if others else "."))
    print("¿Cuánto historial quieres que analice? Stockfish mira cada jugada.\n")
    for i, opt in enumerate(o["options"], 1):
        games = ("~" if opt.get("estimated") else "") + f"{opt['games']:,}".replace(",", ".")
        mins = "ya analizado" if not opt["positions"] else f"~{opt['minutes']} min"
        mark = "  ← recomendado" if opt["window"] == o["recommended"] else ""
        print(f"  {i}. {opt['label']:<22} {games:>6} partidas  {mins:>13}{mark}")
    default = next(i for i, opt in enumerate(o["options"], 1) if opt["window"] == o["recommended"])
    valid = [str(i) for i in range(1, len(o["options"]) + 1)]
    pick = input(f"\nElige {valid[0]}-{valid[-1]} (Enter: {default}) > ").strip() or str(default)
    while pick not in valid:
        pick = input(f"{valid[0]}-{valid[-1]} > ").strip() or str(default)
    return o["options"][int(pick) - 1]["window"]


USERNAME = re.compile(r"[A-Za-z0-9_-]{3,25}")


def player_exists(user: str) -> bool:
    try:
        core.fetch_games.get(f"https://api.chess.com/pub/player/{user}")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise Unreachable(f"chess.com respondió {e.code}.") from None
    except (urllib.error.URLError, TimeoutError, RuntimeError) as e:
        raise Unreachable(f"No llego a chess.com: {e}") from None


def ensure_user(user: str | None = None, interactive: bool = False) -> str:
    """The chess.com player this folder follows: given with --user, already configured, or asked. A new one is
    checked against chess.com and saved to config.json, so nobody has to edit a file."""
    cfg = dict(core.config.load())
    env = (os.environ.get("TEACHESS_USER") or "").strip().lower()
    if env and user and user.strip().lower() != env:
        raise SetupError(f"TEACHESS_USER dice «{env}»: quítala o usa --user {env}.")
    current = (env or cfg.get("user") or "").lower()
    if not user and current and current != "your_chesscom_username":
        return current
    # The folder's player: recorded by sync, or for older folders the configured one if games are already here
    owner = core.folder_player() or (current if current and core.GAMES.exists() else None)
    while True:
        if not user:
            if not interactive:
                raise SetupError("Dime tu usuario de chess.com: teachess setup --user TU_USUARIO")
            user = input("¿Cuál es tu usuario de chess.com? > ").strip()
            continue
        user = user.strip().lower()
        problem = None
        if not USERNAME.fullmatch(user):
            problem = f"«{user}» no parece un usuario de chess.com (letras, números, - y _)."
        elif owner and user != owner:
            raise SetupError(f"Tus datos de teachess ya son de {owner}. Para seguir a {user}, "
                             f"usa otra carpeta de datos: TEACHESS_HOME=~/teachess-{user} teachess setup")
        elif not player_exists(user):
            problem = f"chess.com no conoce al jugador «{user}»."
        if not problem:
            break
        if not interactive:
            raise SetupError(problem)
        print(problem)
        user = None
    cfg["user"] = user
    core.config.save(cfg)
    return user


def tidy(log=print) -> list[dict]:
    """Fix rows older versions stored (cut games, from-position games) and return the games."""
    if n := core.upgrade_games():
        log(f"{n} partidas releídas enteras (antes se guardaban solo las 20 primeras jugadas).")
    if n := core.drop_from_position():
        log(f"{n} partidas que no empiezan en la posición inicial, fuera del análisis.")
    return core.load_games()


def prepare(user: str, time_class: str | None = None, log=print, show: bool = False) -> tuple[list[dict], dict]:
    """Download what the options need, newest first: the last three months, then a year, then three, then all,
    stopping as soon as a window holds enough games of the player's time control to be recommended. Older
    windows are estimated (see `estimate`). The first step always syncs, so the current month is fresh.
    Returns the games and the survey."""
    s = survey(user)
    if not s["totals"]:  # no shortcut: the whole archive, as versions before 0.1.0 did
        log("Descargando tus partidas de chess.com…")
        core.sync(user, log=log, analyse=False, show=show)
        return tidy(log), s
    if not show:
        log("Descargando tus últimas partidas…")
    for i, w in enumerate(("3m", "1y", "3y", "all")):
        if i == 0 or covered(s) > month(w):
            core.sync(user, log=log, analyse=False, since_month=month(w), show=show)
        games = tidy(log)
        since = core.window_since(w)
        recent = Counter(g["time_class"] for g in games if g["end"] >= since)
        tc = time_class or max(core.TIME_CLASSES, key=lambda t: (recent[t], s["totals"][t]))
        if recent[tc] >= MIN_GAMES:
            break
    return games, s


def run(window: str | None = None, time_class: str | None = None, interactive: bool = False, log=print,
        user: str | None = None) -> dict:
    """Make sure there is a player, fetch the archive, pick the window (asked, given or recommended), save it
    and analyse it."""
    if window and window not in core.WINDOWS:
        raise UsageError(f"Ventana desconocida: {window}. Usa una de {', '.join(core.WINDOWS)}.")
    if not shutil.which("stockfish"):
        raise SetupError("Stockfish no está instalado (brew install stockfish o apt install stockfish).")
    user = ensure_user(user, interactive)
    games, s = prepare(user, time_class, log, show=interactive)
    if not games:
        raise SetupError("No hay partidas en tu archivo de chess.com.")
    if window is None:
        log("Midiendo lo que tardaría Stockfish en tu ordenador…")
    o = options(games, time_class, measure=window is None, s=s)
    if not window:
        window = ask(o) if interactive and o["ask"] else o["recommended"]
    if s["totals"] and month(window) < covered(s):  # an older window: download just what it reaches
        core.sync(user, log=log, analyse=False, since_month=month(window), show=interactive)
        games = tidy(log)
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
    core.rebuild_report(show=interactive)
    report = core.load_report()
    return {"window": window, "time_class": o["time_class"], "games": len(gs), "since": since,
            "minutes": round((time.monotonic() - start) / 60, 1),
            "puzzles": len(report.get("puzzles", [])), "drills": len(report.get("drills", [])),
            "blunders_per_game": report.get("blunders", {}).get("per_game"),
            "date": date.today().isoformat()}
