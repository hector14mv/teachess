"""Edge cases of the plan-aware drill deck: no network, no real progress file.

Run: .venv/bin/python tests/test_drills.py
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from teachess import core, drills  # noqa: E402

drills.STATE = pathlib.Path(tempfile.mkdtemp()) / "drill_state.json"
START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
CRAMPED = "k7/8/8/8/8/1q4p1/7P/K7 w - - 0 1"  # white has exactly h3, h4 and hxg3


def card(fen, played, best, line):
    return {"fen": fen, "played": played, "best": best, "line": line, "color": "white", "opening": "Test",
            "times": 5, "reached": 9, "eval_best": 20, "loss": 120, "example": ""}


REPORT = {"drills": [card(START, "a3", "e4", []), card(CRAMPED, "h3", "hxg3", ["x"])]}
PLAN = {}
core.config.load = lambda: {"advice": PLAN}

ok = True


def expect(name, got, want):
    global ok
    ok &= got == want
    print(("PASS" if got == want else "FAIL"), name, got, "want", want)


# An illegal plan move is ignored instead of breaking the deck
PLAN["white|"] = {"move": "Nf6", "text": "typo"}
deck = drills.deck(REPORT)
expect("illegal plan ignored", deck[0]["best"], "e4")
expect("illegal plan card presents", len(drills.present(deck[0])["choices"]), 3)

for bad in ("--", "0000"):
    PLAN["white|"] = {"move": bad, "text": "null move"}
    expect(f"null move {bad} ignored", drills.deck(REPORT)[0]["best"], "e4")

# Changing the plan changes the right answer and the choices of a card that keeps its id
PLAN["white|"] = {"move": "d4", "text": "plan d4"}
first = drills.deck(REPORT)[0]
expect("plan move is the answer", first["best"], "d4")
expect("plan move offered", "d4" in drills.choices(first), True)
expect("Stockfish's favourite left out", "e4" in drills.choices(first), False)
PLAN["white|"] = {"move": "c4", "text": "plan c4"}
again = drills.deck(REPORT)[0]
expect("same id after plan change", again["id"], first["id"])
expect("new plan move offered", "c4" in drills.choices(again), True)
expect("choices rebuilt for the new plan", drills.choices(again) != drills.choices(first), True)

# A cramped position with the engine's move excluded offers two moves instead of crashing
PLAN["white|x"] = {"move": "h4", "text": "plan h4"}
cramped = drills.deck(REPORT)[1]
expect("cramped choices", sorted(drills.choices(cramped)), ["h3", "h4"])
expect("cramped answer graded", drills.answer(cramped, "h4")["correct"], True)

# The planned move itself is not drilled
PLAN["white|x"] = {"move": "h3", "text": "keep h3"}
expect("planned move dropped", len(drills.deck(REPORT)), 1)

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
