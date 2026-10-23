"""Scenario tests for teachess.watch against synthetic games: no network, no real state.

Run: .venv/bin/python tests/test_watch.py
"""
import pathlib, sys, tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from teachess import watch, core, drills

TMP = pathlib.Path(tempfile.mkdtemp())
watch.STATE = TMP / "state.json"; watch.LOCK = TMP / "lock"
MAD = ZoneInfo("Europe/Madrid")
GAMES = []
core.load_games = lambda: list(GAMES)
core.load_report = lambda: {"habits": {"after": {"tras perder": {"score": 46.6}}}, "drills": []}
core.load_evals = lambda: {}
drills.queue = lambda report=None: ([], {"due": 3})

IDS = iter(range(1, 10**6))


def game(t, score):
    return {"url": f"g{next(IDS)}", "end": t.isoformat(), "time_class": "rapid", "score": score, "color": "white",
            "how": "resigned", "opening": "", "opp": "x", "opp_rating": 1, "my_rating": 1, "plies": 30, "sans": []}

def run(at):
    sent = []
    ev = watch.check(live=False, at=at, send=lambda t, b, u: sent.append(t) or True)
    return [e["type"] for e in ev], len(sent)

ok = True
def expect(name, got, want):
    global ok
    ok &= got == want
    print(("PASS" if got == want else "FAIL"), name, got, "want", want)

# 5: no games at first run, then a game -> notified
watch.STATE.unlink(missing_ok=True)
t0 = datetime(2026, 10, 25, 1, 0, tzinfo=MAD)
expect("init with no games", run(t0), ([], 0))
GAMES.append(game(t0 + timedelta(minutes=20), 1))
expect("first game after empty start", run(t0 + timedelta(minutes=25)), (["game"], 1))

# 2: DST — 2026-10-25 03:00 CEST -> 02:00 CET. A game at 02:50+02:00, then one at 02:10+01:00 (later in time)
a = datetime.fromisoformat("2026-10-25T02:50:00+02:00"); b = datetime.fromisoformat("2026-10-25T02:10:00+01:00")
GAMES.append(game(a, 1)); GAMES.append(game(b, 1))
expect("both DST games once", run(b + timedelta(minutes=2)), (["game", "game"], 2))
expect("no repeat after DST", run(b + timedelta(minutes=4)), ([], 0))

# 3: five games between checks -> 3 lessons + a summary
base = b + timedelta(minutes=10)
for i in range(5):
    GAMES.append(game(base + timedelta(minutes=i * 12), 1))
expect("five new games, five lessons", run(base + timedelta(minutes=60)), (["game"] * 5, 5))

# 4: one stop per streak, even at the 3rd and 4th defeat
s0 = base + timedelta(minutes=70)
for i in range(4):
    GAMES.append(game(s0 + timedelta(minutes=i * 10), 0))
    got = run(s0 + timedelta(minutes=i * 10 + 2))
    expect(f"defeat {i + 1}", got[0], ["game", "stop"] if i == 1 else ["game"])
GAMES.append(game(s0 + timedelta(minutes=45), 1))
GAMES.append(game(s0 + timedelta(minutes=55), 0)); GAMES.append(game(s0 + timedelta(minutes=65), 0))
expect("new streak alerts again", run(s0 + timedelta(minutes=67))[0], ["game", "game", "game", "stop"])

# 1: a failing send keeps the state, so the next check repeats instead of losing
GAMES.append(game(s0 + timedelta(minutes=120), 1))
before = watch.STATE.read_text()
try:
    watch.check(live=False, at=s0 + timedelta(minutes=122), send=lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
except RuntimeError:
    pass
expect("state untouched after failed send", watch.STATE.read_text() == before, True)
watch.check(live=False, at=s0 + timedelta(minutes=123), send=lambda *a: False)  # every channel down
expect("state untouched when nobody was reached", watch.STATE.read_text() == before, True)
expect("retried next check", run(s0 + timedelta(minutes=124)), (["game"], 1))

# 6: truncated state file -> start over, no crash
for bad in ('{"last_en', "null", "[]", '{"last_end": "broken"}', '{"last_end": 5}',
            '{"last_end": "2026-10-23T10:00:00"}'):
    watch.STATE.write_text(bad)
    expect(f"bad state {bad} reinit", run(s0 + timedelta(minutes=126)), ([], 0))

# 7: a second check while one holds the lock returns nothing
import fcntl
with open(watch.LOCK, "w") as l:
    fcntl.flock(l, fcntl.LOCK_EX)
    GAMES.append(game(s0 + timedelta(minutes=127), 1))
    expect("locked check skipped", run(s0 + timedelta(minutes=128)), ([], 0))
expect("unlocked check tells", run(s0 + timedelta(minutes=129)), (["game"], 1))
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
