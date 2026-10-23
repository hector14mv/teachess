"""Whole-game analysis: blunder detection and the per-player setup options. Synthetic data, no network.

Run: .venv/bin/python tests/test_analysis.py
"""
import pathlib
import sys
from datetime import date, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import chess  # noqa: E402

import build_report  # noqa: E402
from teachess import core, setup  # noqa: E402

ok = True


def expect(name, got, want):
    global ok
    ok &= got == want
    print(("PASS" if got == want else "FAIL"), name, got, "want", want)


# --- winning chances: the same centipawn loss matters less when the game is already decided ---
wc = build_report.win_chances
expect("even is zero", round(wc(0), 3), 0.0)
expect("symmetric", round(wc(300) + wc(-300), 6), 0.0)
expect("+0,5 -> -2 is a blunder", wc(50) - wc(-200) >= build_report.BLUNDER_DROP, True)
expect("+9 -> +6 is not", wc(900) - wc(600) >= build_report.BLUNDER_DROP, False)

# --- blunders over a whole game, from an evaluation map ---------------------------------------
sans = ["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#"]
evals, board = {}, chess.Board()
cps = [20, 30, 10, 20, 30, 30, 900, 1000]  # black's 3...Nf6?? allows mate
evals[core.key(board)] = {"cp": cps[0], "best": "e4"}
for i, san in enumerate(sans):
    board.push_san(san)
    evals[core.key(board)] = {"cp": cps[i + 1], "best": "g6" if i == 4 else "Nf6"}
black = {"color": "black", "sans": sans}
found, complete = build_report.blunders(black, evals)
expect("black's blunder found", [b["played"] for b in found], ["Nf6"])
expect("its best move", found[0]["best"], "g6")
expect("complete game", complete, True)
expect("white made none", build_report.blunders({"color": "white", "sans": sans}, evals)[0], [])
del evals[core.key(board)]
expect("missing position -> incomplete", build_report.blunders(black, evals)[1], False)

truncated = {"color": "black", "sans": sans[:4], "plies": len(sans)}
expect("row cut at 40 plies is never complete", build_report.blunders(truncated, evals)[1], False)

# --- puzzle ids never collide with the opening card of the same position and move -----------
from teachess import drills  # noqa: E402
same = {"fen": "x 0 1", "played": "Nf6"}
expect("puzzle id differs", drills.card_id(same) != drills.card_id({**same, "kind": "puzzle"}), True)

# --- setup options per player -------------------------------------------------------------------
core.load_evals = lambda: {}


def games(n_by_tc_per_month: dict, months: int) -> list:
    out = []
    for m in range(months, 0, -1):
        for tc, n in n_by_tc_per_month.items():
            for i in range(n):
                end = date.today() - timedelta(days=30 * m - i % 28)
                out.append({"time_class": tc, "end": end.isoformat() + "T12:00:00+02:00", "sans": []})
    out.sort(key=lambda g: g["end"])
    return out


heavy = setup.options(games({"rapid": 120, "blitz": 5}, 60), measure=False)
expect("heavy player: rapid", heavy["time_class"], "rapid")
expect("heavy player: 3 months recommended", heavy["recommended"], "3m")
expect("heavy player is asked", heavy["ask"], True)
expect("heavy player options", [o["window"] for o in heavy["options"]], ["3m", "1y", "3y", "all"])

light = setup.options(games({"rapid": 10}, 60), measure=False)
expect("light player: 3 years recommended", light["recommended"], "3y")

blitzer = setup.options(games({"blitz": 200, "rapid": 20}, 24), measure=False)
expect("blitz player coached in blitz", blitzer["time_class"], "blitz")
expect("two-year account: no duplicate of 3y as all", [o["window"] for o in blitzer["options"]], ["3m", "1y", "3y"])

tiny = setup.options(games({"rapid": 15}, 10), measure=False)
expect("tiny archive: not asked", tiny["ask"], False)
expect("tiny archive: everything", tiny["recommended"], tiny["options"][-1]["window"])

# --- re-reading cut rows merges: a game missing from the cache is kept, marked, and not retried ---
import json  # noqa: E402
import tempfile  # noqa: E402
from teachess import core as core_mod  # noqa: E402
import fetch_games  # noqa: E402
import parse_games  # noqa: E402
tmp = pathlib.Path(tempfile.mkdtemp())
core_mod.GAMES, fetch_games.RAW = tmp / "games.jsonl", tmp / "raw"
fetch_games.RAW.mkdir()
parse_games.USER = "me"
pgn = '[Event "x"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *'
raw = {"url": "u1", "pgn": pgn, "rules": "chess", "end_time": 1760000000, "time_class": "rapid",
       "time_control": "600", "white": {"username": "me", "rating": 1, "result": "win"},
       "black": {"username": "x", "rating": 1, "result": "resigned"}}
(fetch_games.RAW / "2026-10.json").write_text(json.dumps({"games": [raw]}))
core_mod.REANALYSE = tmp / "reanalyse.flag"
rows = [{"url": "u1", "end": "2026-10-09T10:00:00+02:00", "sans": ["e4"], "plies": 6, "my_acc": 98.5},
        {"url": "gone", "end": "2026-10-10T10:00:00+02:00", "sans": ["d4"], "plies": 30},
        {"url": "whole", "end": "2026-10-11T10:00:00+02:00", "sans": ["c4"], "plies": 1, "my_acc": 77.0}]
core_mod.GAMES.write_text("".join(json.dumps(r) + "\n" for r in rows))
core_mod.load_games = lambda: [json.loads(line) for line in core_mod.GAMES.read_text().splitlines()]
expect("recovered a cut game", core_mod.upgrade_games(), True)
after = {g["url"]: g for g in core_mod.load_games()}
expect("whole game now", len(after["u1"]["sans"]), 6)
expect("game missing from cache kept", "gone" in after, True)
expect("and marked", after["gone"].get("cut"), True)
expect("fields the cache lacks are kept", after["u1"]["my_acc"], 98.5)
expect("complete rows untouched", after["whole"]["my_acc"], 77.0)
expect("re-analysis flagged", core_mod.REANALYSE.exists(), True)
expect("no retry next time", core_mod.upgrade_games(), False)
back = {**raw, "url": "gone"}  # sync writes a fresh month to the cache before re-reading
(fetch_games.RAW / "2026-10.json").write_text(json.dumps({"games": [raw, back]}))
expect("a fresh download brings it back", core_mod.upgrade_games([back]), True)
expect("no longer marked", any(g.get("cut") for g in core_mod.load_games()), False)

# --- a window with no games is refused before anything is saved --------------------------------
from teachess.errors import SetupError  # noqa: E402
old_games = games({"rapid": 50}, 30)[:100]  # only games from more than a year ago
core.sync = lambda **kw: []
core.upgrade_games = lambda: False
core.load_games = lambda: old_games
saved = []
core.config.save = saved.append
try:
    setup.run(window="3m")
    expect("empty window refused", "no error", "SetupError")
except SetupError:
    expect("empty window refused", "SetupError", "SetupError")
expect("nothing saved", saved, [])

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
