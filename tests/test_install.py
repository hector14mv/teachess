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

# --- covered: from which month the archive is downloaded ---
raw = pathlib.Path(tempfile.mkdtemp())
core.fetch_games.RAW = raw
survey = {"months": {m: f"u/{m}" for m in ["2024-01", "2024-02", "2026-09", "2026-10"]},
          "totals": {"rapid": 100, "blitz": 0, "bullet": 0, "daily": 0}}
check("nothing downloaded", setup.covered(survey) >= "2026-10", True)
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

# --- the progress bar: a line per quarter off a terminal, silent with --json ---
out = io.StringIO()
bar = progress.Bar("Analizando", 8, stream=out)
for i in range(1, 9):
    bar.update(i)
bar.close()
check("quarters", out.getvalue().splitlines(), ["Analizando: 25 %", "Analizando: 50 %", "Analizando: 75 %",
                                                 "Analizando: 100 %"])
progress.QUIET = True
out = io.StringIO()
bar = progress.Bar("Analizando", 8, stream=out)
bar.update(8)
bar.close()
check("quiet", out.getvalue(), "")

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
