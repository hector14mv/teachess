"""The username step of teachess setup: asked or given, checked, saved. No network, no real config.

Run: .venv/bin/python tests/test_setup.py
"""
import builtins
import os
import pathlib
import sys
import tempfile

os.environ["TEACHESS_HOME"] = tempfile.mkdtemp()  # never the real games or config
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from teachess import core, setup  # noqa: E402
from teachess.errors import SetupError  # noqa: E402

ok = True


def expect(name, got, want):
    global ok
    ok &= got == want
    print(("PASS" if got == want else "FAIL"), name, got, "want", want)


def raises(fn):
    try:
        fn()
        return None
    except SetupError as e:
        return str(e)


os.environ.pop("TEACHESS_USER", None)
tmp = pathlib.Path(tempfile.mkdtemp())
store = {}
core.config.load = lambda: dict(store)
core.config.save = lambda data: store.update(data)
core.GAMES = tmp / "games.jsonl"
core.PLAYER = tmp / "player"
setup.player_exists = lambda u: u in {"magnus", "hector_14"}

expect("non-interactive without a user says how", raises(lambda: setup.ensure_user()),
       "Dime tu usuario de chess.com: teachess setup --user TU_USUARIO")
expect("unknown player refused", raises(lambda: setup.ensure_user("nadie_aqui")),
       "chess.com no conoce al jugador «nadie_aqui».")
expect("not a username", "no parece" in raises(lambda: setup.ensure_user("a b/../x")), True)
expect("given user saved, lowercased", (setup.ensure_user("Magnus"), store.get("user")), ("magnus", "magnus"))
expect("configured user reused", setup.ensure_user(), "magnus")

answers = iter(["", "nadie_aqui", "Hector_14"])
builtins.input = lambda prompt="": next(answers)
store.clear()
expect("asked until a real player", setup.ensure_user(interactive=True), "hector_14")

core.GAMES.write_text("{}\n")
expect("folder of another player refused", "ya son de hector_14" in raises(lambda: setup.ensure_user("magnus")), True)
expect("same player fine", setup.ensure_user("hector_14"), "hector_14")

core.GAMES.unlink()
core.PLAYER.write_text("hector_14\n")  # the folder's player, recorded by sync
store.clear()
expect("recorded player wins over an empty config", "ya son de hector_14" in raises(lambda: setup.ensure_user("magnus")), True)
core.PLAYER.unlink()
core.GAMES.write_text("{}\n")

os.environ["TEACHESS_USER"] = "magnus"
expect("--user against TEACHESS_USER refused", raises(lambda: setup.ensure_user("hector_14")),
       "TEACHESS_USER dice «magnus»: quítala o usa --user magnus.")
expect("TEACHESS_USER alone is the player", setup.ensure_user(), "magnus")
os.environ.pop("TEACHESS_USER")

store.clear(); store["user"] = "your_chesscom_username"
core.GAMES.unlink()
expect("example placeholder is not a user", raises(lambda: setup.ensure_user()),
       "Dime tu usuario de chess.com: teachess setup --user TU_USUARIO")

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
