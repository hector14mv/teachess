"""Installing and setting up: the move of an old checkout's data, the quick setup options and the progress bar.
No network, no real games.

Run: .venv/bin/python tests/test_install.py
"""
import io
import json
import os
import pathlib
import sys
import tempfile

os.environ["TEACHESS_HOME"] = tempfile.mkdtemp()  # never the real games or config
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from teachess import core, paths, progress, setup  # noqa: E402

ok = True


def check(name, got, want):
    global ok
    good = got == want
    ok &= good
    print(("PASS" if good else "FAIL"), name, got, "want", want)


# --- migrate: an old checkout's data/ and config.json move once, and only what teachess wrote ---
tmp = pathlib.Path(tempfile.mkdtemp())
legacy, home = tmp / "checkout", tmp / "home"
(legacy / "data" / "raw").mkdir(parents=True)
(legacy / "data" / "raw" / "2026-10.json").write_text("{}")
(legacy / "data" / "games.jsonl").write_text("{}\n")
(legacy / "data" / "portfolio").mkdir()  # not teachess's: stays
(legacy / "config.json").write_text('{"user": "me"}')
paths.LEGACY, paths.HOME, paths.DATA, paths.CONFIG = legacy, home, home / "data", home / "config.json"
check("explicit TEACHESS_HOME never migrates", paths.migrate(log=lambda m: None), False)
saved = os.environ.pop("TEACHESS_HOME")
check("first run moves", paths.migrate(log=lambda m: None), True)
check("games moved", (home / "data" / "games.jsonl").exists(), True)
check("months moved", (home / "data" / "raw" / "2026-10.json").exists(), True)
check("config moved", json.loads((home / "config.json").read_text()), {"user": "me"})
check("own files stay", (legacy / "data" / "portfolio").exists(), True)
check("second run does nothing", paths.migrate(log=lambda m: None), False)

# a move cut short: some items already in the staging folder, the rest still in the checkout
legacy2, home2 = tmp / "checkout2", tmp / "home2"
(legacy2 / "data").mkdir(parents=True)
(legacy2 / "data" / "evals.json").write_text("{}")
(home2 / "data.migrating").mkdir(parents=True)
(home2 / "data.migrating" / "games.jsonl").write_text("{}\n")
paths.LEGACY, paths.HOME, paths.DATA, paths.CONFIG = legacy2, home2, home2 / "data", home2 / "config.json"
check("cut move resumes", paths.migrate(log=lambda m: None), True)
check("resumed with both", sorted(p.name for p in (home2 / "data").iterdir()), ["evals.json", "games.jsonl"])
os.environ["TEACHESS_HOME"] = saved

# a copy across disks lands whole: os.rename fails with EXDEV, the copy goes through a .partial name
import errno  # noqa: E402
real_rename, calls = os.rename, []


def cross_disk(src, dst):
    calls.append(pathlib.Path(dst).name)
    if not str(dst).endswith(".partial") and len(calls) == 1:
        raise OSError(errno.EXDEV, "cross-device link")
    return real_rename(src, dst)


src_dir = tmp / "src_raw"
src_dir.mkdir()
(src_dir / "2026-10.json").write_text("{}")
os.rename = cross_disk
paths._move(src_dir, tmp / "dst_raw")
os.rename = real_rename
check("cross-disk copy via .partial", calls, ["dst_raw", "dst_raw"])
check("cross-disk copy complete", (tmp / "dst_raw" / "2026-10.json").exists() and not src_dir.exists(), True)

# --- covered: from which month the archive is downloaded ---
raw = pathlib.Path(tempfile.mkdtemp())
core.fetch_games.RAW = raw
survey = {"months": {m: f"u/{m}" for m in ["2024-01", "2024-02", "2026-09", "2026-10"]},
          "totals": {"rapid": 100, "blitz": 0, "bullet": 0, "daily": 0}}
check("nothing downloaded", setup.covered(survey), "9999-99")
for m in ["2026-09", "2026-10"]:
    (raw / f"{m}.json").write_text(json.dumps({"games": [{"time_class": "rapid"}] * 10}))
check("newest two", setup.covered(survey), "2026-09")
for m in ["2024-01", "2024-02"]:
    (raw / f"{m}.json").write_text(json.dumps({"games": [{"time_class": "rapid"}] * 10}))
check("whole archive", setup.covered(survey), "0000-00")

# --- estimate: sizes over bytes per game, times the time control's share of the rest ---
(raw / "2024-01.json").unlink()
(raw / "2024-02.json").unlink()
sizes = {"u/2026-09": 1000, "u/2026-10": 1000, "u/2024-01": 3000, "u/2024-02": 1000}
core.fetch_games.size = lambda url: sizes[url]
survey["totals"] = {"rapid": 60, "blitz": 20, "bullet": 0, "daily": 0}  # 20 rapid downloaded: 40 rapid, 20 blitz left
check("estimate", setup.estimate(survey, "rapid", "2024-01", "2026-09"), round(4000 / 100 * 40 / 60))

# --- options: a window not downloaded yet never looks analysed, even when the known one is ---
import chess  # noqa: E402
from datetime import date  # noqa: E402

today = date.today().isoformat() + "T12:00:00+02:00"
games = [{"time_class": "rapid", "end": today, "sans": ["e4", "e5", "Nf3"], "url": f"g{i}"} for i in range(3)]
evaluated = {}
board = chess.Board()
evaluated[core.key(board)] = {"cp": 0, "best": "e4"}
for san in ["e4", "e5", "Nf3"]:
    board.push_san(san)
    evaluated[core.key(board)] = {"cp": 0, "best": None}
core.load_evals = lambda: evaluated
setup.covered = lambda s: setup.month("3m")
setup.estimate = lambda s, tc, since, cover: 50
current = date.today().strftime("%Y-%m")
survey = {"months": {"2020-01": "a", current: "b"}, "totals": {"rapid": 500, "blitz": 0, "bullet": 0, "daily": 0}}
opts = {o["window"]: o for o in setup.options(games, measure=False, s=survey)["options"]}
check("known window analysed", opts["3m"]["positions"], 0)
check("older window still has work", opts["1y"]["positions"] > 0 and opts["1y"]["estimated"], True)
check("all from chess.com's totals", (opts["all"]["games"], opts["all"]["estimated"]), (500, False))

# --- the progress bar: a line per quarter off a terminal, silent with --json ---
out = io.StringIO()
bar = progress.Bar("Analizando", 8, stream=out)
for i in range(1, 9):
    bar.update(i)
bar.close()
check("quarters", out.getvalue().splitlines(), ["Analizando: 25 %", "Analizando: 50 %", "Analizando: 75 %",
                                                 "Analizando: 100 %"])
# on a terminal the line always fits, or it wraps and every update lands on a new line
for cols in (120, 72, 50, 30, 12):
    line = progress.render("Comprobando a fondo las jugadas buenas", 21, 100, ["quedan ~1 min", "21/100 jugadas"], cols)
    check(f"fits in {cols} columns", len(line) <= cols - 1, True)
check("wide keeps everything", progress.render("Analizando", 5, 10, ["quedan ~1 min"], 120),
      "Analizando  " + "█" * 12 + "░" * 12 + "   50 %  · quedan ~1 min")
check("narrow drops the count first",
      "quedan" in progress.render("Analizando tus partidas", 5, 10, ["quedan ~1 min", "5/10 meses"], 60)
      and "meses" not in progress.render("Analizando tus partidas", 5, 10, ["quedan ~1 min", "5/10 meses"], 60), True)

progress.QUIET = True
out = io.StringIO()
bar = progress.Bar("Analizando", 8, stream=out)
bar.update(8)
bar.close()
check("quiet", out.getvalue(), "")

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
