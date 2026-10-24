"""teachess dash: a local dashboard on 127.0.0.1 that watches chess.com and reacts to each new game."""

from __future__ import annotations

import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import core, drills

STATIC = Path(__file__).parent / "static"
POLL_SECONDS = 120


class Store:
    """In-memory copy of the data files, refreshed after each sync."""

    def __init__(self, live: bool, at=None):
        self.lock = threading.RLock()
        self.live = live
        self.at = at  # replay a past moment instead of now
        self.syncing = False
        self.last_check = None
        self.error = None
        self._reviews: dict[tuple, dict] = {}  # (game url, deep cache version) -> review
        self.reload()

    def reload(self) -> None:
        with self.lock:
            self.games = core.load_games()
            self.report = core.load_report()
            self.evals = core.load_evals()
            self.version = str(core.REPORT.stat().st_mtime) if core.REPORT.exists() else "0"
            self._reviews.clear()
            self._insights = None

    def review(self, g: dict) -> dict:
        with self.lock:
            # Keyed by the deep cache's version too: the watcher may confirm this game's blunders later
            deep = core.DEEP.stat().st_mtime if core.DEEP.exists() else 0
            k = (g["url"], deep)
            if k not in self._reviews:
                self._reviews[k] = core.review(g, self.evals, self.report, deep="cached")
            return self._reviews[k]

    def state(self) -> dict:
        with self.lock:
            games = [g for g in self.games if self.at is None or core.end_dt(g) <= self.at]
            rapid = [g for g in games if g["time_class"] == core.tc()]
            t = core.today(games, self.report, at=self.at)
            habits = self.report.get("habits", {})
            after = habits.get("after", {})
            band = habits.get("band", {})
            context = [
                {"label": "Después de ganar", "score": after.get("tras ganar", {}).get("score"), "now": t["state"] in ("win", "draw")},
                {"label": "Primera de la sesión", "score": after.get("primera de la sesión", {}).get("score"), "now": t["state"] == "idle"},
                {"label": "Después de perder", "score": after.get("tras perder", {}).get("score"), "now": t["state"] in ("loss", "streak")},
                {"label": f"{core.BAND_PHRASE[t['band']]} (ahora)", "score": band.get(t["band"], {}).get("score"), "now": False},
            ]
            latest = None
            if rapid:
                latest = {**core.describe(rapid[-1]), "review": self.review(rapid[-1])}
            return {
                "user": DISPLAY_NAME, "rating": core.rating_now(games), "today": t,
                "games": [core.describe(g) for g in rapid[-7:]][::-1], "latest": latest,
                "context": context, "recent": self.report.get("totals", {}).get("recent"),
                "version": self.version,
                "watcher": {"live": self.live, "replay": self.at.isoformat() if self.at else None, "syncing": self.syncing, "last_check": self.last_check,
                            "error": self.error, "interval": POLL_SECONDS},
            }

    def insights(self) -> dict:
        with self.lock:
            if self._insights is None:
                since = self.report.get("since", core.window_since())
                habits = self.report.get("habits", {})
                games = [g for g in self.games if self.at is None or core.end_dt(g) <= self.at]
                self._insights = {
                    "since": since, "totals": self.report.get("totals", {}),
                    "tree": self.report.get("tree", {}), "names": core.branch_names(games, since),
                    "monthly": core.monthly_rating(games), "first_mistake": self.report.get("first_mistake", {}),
                    "heat": core.heatmap(games, since),
                    "clock": {"loss_median": habits.get("clock_left_median", {}).get("loss"),
                              "over_3min": habits.get("losses_over_3min_left")},
                    "accuracy": habits.get("accuracy", {}),
                    "advice": core.config.load().get("advice", {}),
                }
            return self._insights

    def drill(self) -> dict:
        with self.lock:
            due, stats = drills.queue(self.report)
            card = drills.present(due[0]) if due else None
            nxt = [{"title": f"{c['opening']} con {core.SIDE[c['color']]}",
                    "line": f"{core.move_label(len(c['line']))}{c['played']} → {c['best']}",
                    "times": "puzle" if c.get("kind") == "puzzle" else f"{c['times']} de {c['reached']}"}
                   for c in due[1:5]]
            return {"card": card, "stats": stats, "queue": nxt}


DISPLAY_NAME = ""


def display_name() -> str:
    """The username with chess.com's capitalisation, read from the newest cached archive."""
    user = core.config.chess_user()
    for f in sorted((core.DATA / "raw").glob("*.json"), reverse=True)[:1]:
        for g in json.loads(f.read_text())["games"]:
            for side in ("white", "black"):
                if g[side]["username"].lower() == user:
                    return g[side]["username"]
    return user


def watch(store: Store, stop: threading.Event) -> None:
    while not stop.is_set():
        store.syncing = True
        try:
            new = core.sync(log=lambda msg: print(f"  ▸ {msg}", flush=True))
            store.error = None
            if new:
                store.reload()
                global DISPLAY_NAME
                DISPLAY_NAME = display_name()  # the first sync is what brings the archive with the real spelling
                rapid = [g for g in new if g["time_class"] == core.tc()]
                if len(new) > 3:
                    print(f"  ▸ {len(new)} partidas nuevas desde la última vez; la más reciente:", flush=True)
                for g in rapid[-3:] if len(new) <= 3 else rapid[-1:]:
                    d = core.describe(g)
                    print(f"  ▸ Partida nueva: {d['result'].lower()} con {d['side']} · {d['opening']} — "
                          f"{store.review(g)['verdict']}", flush=True)
        except Exception as e:  # network down, chess.com 5xx: keep serving and retry next round
            store.error = str(e)
            print(f"  ▸ chess.com no responde ({e}); reintento en {POLL_SECONDS // 60} min", flush=True)
        finally:
            store.syncing = False
            store.last_check = time.time()
        stop.wait(POLL_SECONDS)


def make_handler(store: Store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_json(self, data, status=200):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                body = (STATIC / "dash.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/state":
                self.send_json(store.state())
            elif path == "/api/insights":
                self.send_json(store.insights())
            elif path == "/api/drill":
                self.send_json(store.drill())
            else:
                self.send_json({"error": "not found"}, 404)

        def do_POST(self):
            if self.path != "/api/drill":
                return self.send_json({"error": "not found"}, 404)
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= 4096:
                return self.send_json({"error": "bad request"}, 400)
            try:
                body = json.loads(self.rfile.read(length))
                card_id, san = body["id"], body["san"]
                if not (isinstance(card_id, str) and isinstance(san, str) and len(card_id) <= 16 and len(san) <= 10):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                return self.send_json({"error": "bad request"}, 400)
            with store.lock:
                card = drills.find(card_id, store.report)
                if not card:
                    return self.send_json({"error": "unknown card"}, 404)
                try:
                    self.send_json(drills.answer(card, san))
                except ValueError as e:
                    self.send_json({"error": str(e)}, 400)

    return Handler


def serve(port: int = 4747, open_browser: bool = True, live: bool = True, at=None) -> None:
    global DISPLAY_NAME
    DISPLAY_NAME = display_name()
    live = live and at is None
    store = Store(live, at)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(store))
    url = f"http://localhost:{port}"
    total = len(store.games)
    print(f"  ▸ {total:,} partidas de {DISPLAY_NAME} cargadas".replace(",", "."))
    print(f"  ▸ Panel en {url}" + ("  (abriendo el navegador)" if open_browser else ""))
    stop = threading.Event()
    if live:
        print(f"  ▸ Vigilando chess.com cada {POLL_SECONDS // 60} min · Ctrl+C para salir", flush=True)
        threading.Thread(target=watch, args=(store, stop), daemon=True).start()
    elif at:
        print(f"  ▸ Repetición del {at:%d/%m/%Y a las %H:%M}, sin conexión · Ctrl+C para salir", flush=True)
    else:
        print("  ▸ Sin conexión con chess.com (--offline) · Ctrl+C para salir", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
