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

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
