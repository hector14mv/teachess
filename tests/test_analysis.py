"""Whole-game analysis: blunder detection and the per-player setup options. Synthetic data, no network.

Run: .venv/bin/python tests/test_analysis.py
"""
import os
import pathlib
import tempfile
import sys
from datetime import date, timedelta

os.environ["TEACHESS_HOME"] = tempfile.mkdtemp()  # never the real games or config
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import chess  # noqa: E402

from teachess import build_report  # noqa: E402
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

# --- the deep check replaces the shown best move and drops what is not a blunder at depth -----
real_deep = core.deep_evals
b0 = chess.Board()
for san in sans[:5]:
    b0.push_san(san)
before = core.key(b0)
after_nf6 = b0.copy(); after_nf6.push_san("Nf6")
blunder = {"fen": before + " 0 1", "played": "Nf6", "best": "g6", "before": -30, "after": -900, "loss": 870, "drop": 0.9}
core.deep_evals = lambda keys, compute=True: {before: {"cp": 30, "best": "Qe7"}, core.key(after_nf6): {"cp": 1000, "best": None}}
checked = core.confirm_blunders("black", [blunder])
expect("deep best move shown", checked[0]["best"], "Qe7")
expect("deep evaluations, player side", (checked[0]["before"], checked[0]["after"]), (-30, -1000))
core.deep_evals = lambda keys, compute=True: {before: {"cp": 0, "best": "g6"}, core.key(after_nf6): {"cp": 40, "best": None}}
expect("not a blunder at depth: dropped", core.confirm_blunders("black", [blunder]), [])
core.deep_evals = lambda keys, compute=True: {}
expect("no Stockfish: kept as it was", core.confirm_blunders("black", [blunder]), [blunder])
# opening mistakes use their own threshold: 0.8 pawns lost, not winning chances
core.deep_evals = lambda keys, compute=True: {before: {"cp": -20, "best": "g6"}, core.key(after_nf6): {"cp": 70, "best": None}}
expect("opening mistake that holds at depth", [m["best"] for m in core.confirm("black", [blunder], core.is_mistake)], ["g6"])
core.deep_evals = lambda keys, compute=True: {before: {"cp": -20, "best": "g6"}, core.key(after_nf6): {"cp": 30, "best": None}}
expect("opening mistake of 0.5 pawns at depth: dropped", core.confirm("black", [blunder], core.is_mistake), [])
asked = []
core.deep_evals = lambda keys, compute=True: asked.append(compute) or {}
core.confirm_blunders("black", [blunder], compute=False)
expect("cached-only check never computes", asked, [False])
core.deep_evals = real_deep

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

# --- setup's re-read of cut rows: only cut rows change, old fields survive --------------------
import json  # noqa: E402
import tempfile  # noqa: E402
from teachess import fetch_games  # noqa: E402
from teachess import parse_games  # noqa: E402
tmp = pathlib.Path(tempfile.mkdtemp())
core.GAMES, fetch_games.RAW = tmp / "games.jsonl", tmp / "raw"
fetch_games.RAW.mkdir()
parse_games.USER = "me"
pgn = '[Event "x"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *'
raw = {"url": "u1", "pgn": pgn, "rules": "chess", "end_time": 1760000000, "time_class": "rapid",
       "time_control": "600", "white": {"username": "me", "rating": 1, "result": "win"},
       "black": {"username": "x", "rating": 1, "result": "resigned"}}
(fetch_games.RAW / "2026-10.json").write_text(json.dumps({"games": [raw]}))
rows = [{"url": "u1", "end": "2026-10-09T10:00:00+02:00", "sans": ["e4"], "plies": 6, "my_acc": 98.5,
         "eco": "C60"},
        {"url": "gone", "end": "2026-10-10T10:00:00+02:00", "sans": ["d4"], "plies": 30},
        {"url": "whole", "end": "2026-10-11T10:00:00+02:00", "sans": ["c4"], "plies": 1, "my_acc": 77.0}]
core.GAMES.write_text("".join(json.dumps(r) + "\n" for r in rows))
real_load_games = core.load_games
core.load_games = lambda: [json.loads(line) for line in core.GAMES.read_text().splitlines()]
from_position = {**raw, "url": "fp", "initial_setup": "rnbqkbnr/pp1ppppp/8/2p5/4P3/2N5/PPPP1PPP/R1BQKBNR b KQkq - 2 2"}
expect("from-position game not parsed", parse_games.parse(from_position), None)
expect("standard game parsed", parse_games.parse(raw)["url"], "u1")
fen_only = {**raw, "url": "fp2", "pgn": '[Event "x"]\n[SetUp "1"]\n[FEN "8/8/8/8/8/8/k7/K7 w - - 0 1"]\n\n*'}
expect("from-position by the PGN tag only", parse_games.standard_start(fen_only), False)
in_comment = {**raw, "url": "c1", "pgn": '[Event "x"]\n\n1. e4 { [FEN "8/8/8/8/8/8/k7/K7 w - - 0 1"] } e5 *'}
expect("a FEN inside a comment is not the start", parse_games.standard_start(in_comment), True)
broken_fen = {**raw, "url": "b1", "pgn": '[Event "x"]\n[SetUp "1"]\n[FEN "invalid"]\n\n*'}
expect("an invalid FEN header is not a standard start", parse_games.standard_start(broken_fen), False)
expect("and is not parsed", parse_games.parse(broken_fen), None)
says_standard = {**broken_fen, "url": "b2", "initial_setup": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"}
expect("an invalid FEN header is skipped even if chess.com says standard", parse_games.parse(says_standard), None)
expect("one cut row completed", core.upgrade_games(), 1)
after = {g["url"]: g for g in core.load_games()}
expect("whole game now", len(after["u1"]["sans"]), 6)
expect("fields the cache lacks are kept", (after["u1"]["my_acc"], after["u1"]["eco"]), (98.5, "C60"))
expect("game missing from cache kept as it was", after["gone"], rows[1])
expect("complete rows untouched", after["whole"], rows[2])
expect("nothing left to recover", core.upgrade_games(), 0)
other_opponent = {**raw, "url": "u2", "black": {"username": "y", "rating": 1, "result": "resigned"}}
(fetch_games.RAW / "2026-09.json").write_text(json.dumps({"games": [from_position, other_opponent]}))
with open(core.GAMES, "a") as f:
    f.write(json.dumps({"url": "fp", "end": "2026-09-01T10:00:00+02:00", "sans": ["Nc6"], "plies": 1}) + "\n")
expect("stored from-position row dropped", core.drop_from_position(), 1)
expect("other rows kept", sorted(g["url"] for g in core.load_games()), ["gone", "u1", "whole"])
core.load_games = real_load_games

# --- one player per folder: an old folder's player is read from its cached games -------------
core.PLAYER = tmp / "player"
core.DATA = tmp
expect("old folder: its cached player", core.cached_player(), "me")
try:
    core.claim_folder("someone_else")
    expect("another player refused", "no error", "SetupError")
except core.SetupError:
    expect("another player refused", "SetupError", "SetupError")
same_pair = tmp / "raw_same_pair"
real_raw = fetch_games.RAW
fetch_games.RAW = same_pair; same_pair.mkdir()
(same_pair / "2026-10.json").write_text(json.dumps({"games": [raw]}))
try:
    core.claim_folder("me")
    expect("unclear owner: nobody adopted", "no error", "SetupError")
except core.SetupError:
    expect("unclear owner: nobody adopted", "SetupError", "SetupError")
fetch_games.RAW = real_raw
core.claim_folder("me", record=True)
expect("the folder's player recorded", core.PLAYER.read_text().strip(), "me")

# --- a window with no games is refused before anything is saved --------------------------------
from teachess.errors import SetupError  # noqa: E402
old_games = games({"rapid": 50}, 30)[:100]  # only games from more than a year ago
core.sync = lambda **kw: []
core.upgrade_games = lambda: False
core.drop_from_position = lambda: 0
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
