"""teachess: your chess.com games from the terminal. Every command takes --json for agents.

Each read command is a data function (what --json prints and what the MCP tools return) plus a printer
for people, so the terminal, scripts and agents all see the same numbers.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from datetime import date, datetime, timedelta

import chess

from . import core, drills
from .errors import NotFound, TeachessError, UsageError

USE_COLOR = sys.stdout.isatty()


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def blue(t): return paint(t, "38;5;111")
def orange(t): return paint(t, "38;5;209")
def dim(t): return paint(t, "2")
def bold(t): return paint(t, "1")


def emit(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def parse_since(value: str) -> str:
    m = re.fullmatch(r"(\d+)([dwm])", value)
    if m:
        days = int(m[1]) * {"d": 1, "w": 7, "m": 30}[m[2]]
        return (date.today() - timedelta(days=days)).isoformat()
    date.fromisoformat(value)  # raises on anything else
    return value


def parse_at(value: str) -> datetime:
    at = datetime.fromisoformat(value)
    return at if at.tzinfo else at.replace(tzinfo=core.MADRID)


def result_text(d: dict) -> str:
    r = f"{d['result']:<7}"
    return blue(r) if d["score"] == 1 else orange(r) if d["score"] == 0 else r


def pct_text(x) -> str:
    return "—" if x is None else f"{x:.0f} %"


# --- data: what --json prints and the MCP tools return ---------------------------------------

def data_sync(args) -> list[dict]:
    log = (lambda *_: None) if args.json else print
    if "analysis" not in core.config.load() and not args.json:
        if not core.GAMES.exists() and sys.stdin.isatty():
            # A brand-new install: ask how much history to analyse instead of assuming a year
            from . import setup
            show_setup(setup.run(interactive=True), args)
            return []
        if core.GAMES.exists():
            print(dim("Consejo: teachess setup analiza tus partidas enteras y te deja elegir cuánto historial."))
    return [core.describe(g) for g in core.sync(log=log)]


def games_in(args) -> list[dict]:
    tc = args.tc or core.tc()
    return [g for g in core.load_games() if g["end"] >= args.since and (tc == "all" or g["time_class"] == tc)]


def data_games(args) -> list[dict]:
    return [core.describe(g) for g in games_in(args)[-args.limit:]][::-1]


def data_openings(args) -> list[dict]:
    return [o for o in core.load_report().get("openings", []) if args.color in ("all", o["color"])]


def data_review(args) -> dict:
    g = core.find_game(core.load_games(), args.game)
    if not g:
        raise NotFound(f"No encuentro la partida «{args.game}». Usa una URL de chess.com, su número o «last».")
    core.evaluate_games([g], log=(lambda *_: None) if args.json else print)
    return {**core.describe(g), **core.review(g)}


def data_today(args) -> dict:
    return core.today(at=args.at)


def data_progress(args) -> dict:
    return {**core.progress(months=args.months), "drills": drills.queue()[1]}


def data_drill(args) -> dict:
    if args.answer:
        card_id, san = args.answer
        card = drills.find(card_id)
        if not card:
            raise NotFound(f"No hay ninguna tarjeta «{card_id}». Mira los ids con teachess drill --json.")
        try:
            return drills.answer(card, san)
        except ValueError as e:
            raise UsageError(str(e)) from None
    due, stats = drills.queue()
    return {"stats": stats, "due": [drills.present(c) for c in due[:args.limit]]}


# --- printers ---------------------------------------------------------------------------------

def show_sync(new: list[dict], args) -> None:
    if not new:
        return print("Sin partidas nuevas.")
    for d in new:
        print(f"  {d['when']:<13} {result_text(d)} {d['side']:<8} {d['opening']}")
    print(f"{len(new)} partida{'s' if len(new) > 1 else ''} nueva{'s' if len(new) > 1 else ''}. Informe actualizado.")


def show_games(rows: list[dict], args) -> None:
    if not rows:
        return print("Ninguna partida en ese periodo.")
    for d in rows:
        print(f"{dim(d['when']):<13}  {result_text(d)} {d['side']:<8} {d['opening'][:28]:<28} "
              f"{dim('vs ' + d['opp'] + ' (' + str(d['opp_rating']) + ')')}  {dim(d['how'])}")
    games = games_in(args)
    pts = sum(g["score"] for g in games)
    pts_txt = f"{pts:g}".replace(".", ",")
    print(dim(f"\n{len(games)} partidas desde {args.since}: {pts_txt} puntos ({100 * pts / len(games):.0f} %)."))


def show_openings(rows: list[dict], args) -> None:
    print(dim(f"Rapid desde {core.load_report().get('since')}  ·  % de puntos  ·  % con error antes de la jugada 12\n"))
    for o in rows:
        pct = f"{o['score']:.0f} %"
        pct = blue(pct) if o["score"] >= 52 else orange(pct) if o["score"] <= 47 else pct
        print(f"  {core.SIDE[o['color']]:<8} {o['name']:<24} {o['n']:>4} partidas  {pct:>6}   "
              f"{dim(str(o['mistake_by12']) + ' % con error')}")


def show_review(r: dict, args) -> None:
    print(f"{bold(r['when'])}  {result_text(r)} con {r['side']} vs {r['opp']} ({r['opp_rating']})  ·  {r['opening']}")
    print(f"{r['url']}\n")
    print(orange(r["verdict"]) if r["first"] else blue(r["verdict"]))
    for m in r["mistakes"]:
        rep = f"  · repetido {m['repeated']} de {m['reached']} veces" if m["repeated"] else ""
        print(f"  {m['move']:<9} pierde {core.pawns(m['loss'])}  ·  mejor {m['best_move']}{dim(rep)}")


def show_today(t: dict, args) -> None:
    if t["state"] == "streak":
        print(orange(bold(f"Llevas {t['losses_in_a_row']} derrotas seguidas. Para aquí.")))
    elif t["state"] == "loss":
        print(orange("Acabas de perder."))
    elif t["state"] in ("win", "draw"):
        print(blue(f"Sesión en marcha: {t['session_points']:g} de {t['session_games']}."))
    else:
        print("Sin partidas en la última hora.")
    print(t["advice"])
    if t["last"]:
        last = t["last"]
        print(dim(f"Última: {last['when']} · {last['result'].lower()} con {last['side']} · {last['opening']}"))


def show_progress(p: dict, args) -> None:
    r, y, s = p["rating"], p["year"], p["drills"]
    if r["now"] is None:
        return print(f"Aún no hay partidas {core.tc()}: ejecuta teachess sync.")
    print(f"Elo {core.tc()} {bold(str(r['now']))}  ·  mejor {r['best']} ({r['best_date']})\n")
    print(dim(f"  {'mes':<8} {'partidas':>8} {'puntos':>7} {'elo':>5}  {'error antes de la 12':>20}  "
              f"{'primer error':>12}  {'graves/partida':>14}"))
    for m in p["months"]:
        first = "—" if m["median_first_mistake"] is None else f"jugada {core.move_num(m['median_first_mistake'])}"
        grave = "—" if m["blunders_per_game"] is None else f"{m['blunders_per_game']:.1f}".replace(".", ",")
        print(f"  {m['label']:<8} {m['games']:>8} {pct_text(m['score']):>7} {m['rating']:>5}  "
              f"{pct_text(m['mistake_by12']):>20}  {first:>12}  {grave:>14}")
    first = "" if y["median_first_mistake"] is None else f", primer error hacia la jugada {core.move_num(y['median_first_mistake'])}"
    if y.get("blunders_per_game") is not None:
        first += f", {y['blunders_per_game']:.1f} errores graves por partida".replace(".", ",")
    from .setup import LABELS
    print(dim(f"\n{LABELS[core.analysis()['window']]}: {pct_text(y['score'])} de los puntos, error antes de la jugada 12 en el "
              f"{pct_text(y['mistake_by12'])}{first}."))
    print(dim(f"Tarjetas: {s['learned']} aprendidas de {s['total']}, {s['due']} para hoy."))


def show_drill(res: dict, args) -> None:
    # Only for --answer: the interactive deck runs through drill_loop
    print((blue if res["correct"] else orange)(bold(res["title"])))
    print(res["body"])


def drill_loop(args) -> None:
    due, stats = drills.queue()
    if not due:
        return print(f"Nada pendiente hoy. {stats['learned']} de {stats['total']} tarjetas aprendidas.")
    print(dim(f"{stats['due']} tarjetas para hoy de {stats['total']}. Escribe el número de la jugada; q para salir.\n"))
    for card in due[:args.limit]:
        c = drills.present(card)
        board = chess.Board(c["fen"])
        flip = c["color"] == "black"
        kind = "Puzle" if c["kind"] == "puzzle" else "Tarjeta"
        print(bold(f"{kind} {c['n']} de {stats['total']} · {c['opening']} con {c['side']}"))
        print(dim(c["context"]))
        print(board.unicode(invert_color=True, borders=False, empty_square="·", orientation=not flip))
        print(dim(" ".join("hgfedcba" if flip else "abcdefgh")))
        line = c["line"] if len(c["line"]) < 60 else "… " + c["line"][-50:].split(" ", 1)[-1]
        print(f"\n{line} — {bold(c['ask'])}")
        for i, ch in enumerate(c["choices"], 1):
            print(f"  {i}. {ch['label']}")
        valid = [str(i) for i in range(1, len(c["choices"]) + 1)]
        pick = input("> ").strip().lower()
        while pick not in (*valid, "q"):
            pick = input(f"{', '.join(valid)} o q > ").strip().lower()
        if pick == "q":
            break
        res = drills.answer(card, c["choices"][int(pick) - 1]["san"])
        print((blue if res["correct"] else orange)(bold(res["title"])))
        print(res["body"] + "\n")


# --- actions ----------------------------------------------------------------------------------

def show_setup(r: dict, args) -> None:
    per = "—" if r["blunders_per_game"] is None else f"{r['blunders_per_game']:.1f}".replace(".", ",")
    print(blue(bold(f"\nListo: {r['games']} partidas de {r['time_class']} desde {r['since']}.")))
    print(f"Errores graves por partida: {per}. Tienes {r['drills']} tarjetas de apertura y {r['puzzles']} puzles "
          f"de tus propios errores: teachess drill.")


def cmd_setup(args) -> None:
    from . import setup
    if args.json and not args.window:
        # For agents: the options to show the player; call again with --window to analyse
        with contextlib.redirect_stdout(sys.stderr):
            o = setup.options(core.load_games() or _fetch_all(), args.time_class)
        return emit(o)
    if args.json:
        with contextlib.redirect_stdout(sys.stderr):
            r = setup.run(args.window, args.time_class, log=lambda *_: None)
        return emit(r)
    show_setup(setup.run(args.window, args.time_class, interactive=sys.stdin.isatty() and not args.yes), args)


def _fetch_all() -> list[dict]:
    core.sync(log=lambda *_: None, analyse=False)
    return core.load_games()


def cmd_dash(args) -> None:
    from . import server
    server.serve(port=args.port, open_browser=not args.no_open, live=not args.offline, at=args.at)


def cmd_skill(args) -> None:
    from . import coach
    if not args.action:
        return print(coach.skill_text(), end="")
    status = coach.install_skill(args.dir)
    if args.json:
        return emit(status)
    print(f"  {coach.STATUS_TEXT[status['status']]:<12} {status['path']}")
    if not status["on_path"]:
        print(orange("Ojo: teachess no está en tu PATH, y la skill lo llama por su nombre."))


def cmd_coach(args) -> None:
    from . import coach
    folder = coach.make_folder(args.dir)
    if args.json:
        return emit(folder)
    for f in folder["files"]:
        print(dim(f"  {coach.STATUS_TEXT[f['status']]:<12} {f['path']}"))
    coach.launch(folder["dir"], " ".join(args.prompt), launch=not args.no_launch)


def cmd_watch(args) -> None:
    from . import watch
    if args.action:
        result = {"install": watch.install, "uninstall": watch.uninstall, "status": watch.status}[args.action]()
        if args.json:
            return emit(result)
        if args.action == "status":
            print(f"Servicio: {'en marcha' if result['running'] else 'instalado pero parado' if result['installed'] else 'sin instalar'}")
            for line in result["log"]:
                print(dim(f"  {line}"))
            return
        texts = {"created": "instalado", "updated": "actualizado", "unchanged": "ya estaba instalado",
                 "removed": "desinstalado", "absent": "no estaba instalado"}
        print(f"Servicio de avisos {texts[result['status']]}: {result['plist']}")
        if args.action == "install":
            print(dim("Revisa chess.com cada 2 minutos aunque el panel esté cerrado. Registro: " + result["log"]))
        return
    if args.once or args.at:
        if not args.json:
            watch.check(live=not args.offline, at=args.at)
            return
        with contextlib.redirect_stdout(sys.stderr):
            events = watch.check(live=not args.offline, at=args.at)
        return emit(events)
    watch.loop(live=not args.offline)


def cmd_mcp(args) -> None:
    from . import mcp
    if not args.action:
        return mcp.serve()
    if not args.target:
        raise UsageError("Dime dónde instalarlo: teachess mcp install claude-code | claude-desktop")
    status = mcp.install(args.target)
    if args.json:
        return emit(status)
    print(f"Servidor MCP {mcp.STATUS_TEXT[status['status']]} en {args.target}: {status['config']}")
    if status.get("note"):
        print(dim(status["note"]))


# --- parser -----------------------------------------------------------------------------------

class Parser(argparse.ArgumentParser):
    """Raises instead of exiting, so the MCP server can turn a bad argument into a tool error."""

    def error(self, message):
        e = UsageError(message)
        e.usage = self.format_usage()
        raise e


# name -> (data, printer); the read commands the MCP server exposes come from here
COMMANDS = {
    "sync": (data_sync, show_sync),
    "games": (data_games, show_games),
    "openings": (data_openings, show_openings),
    "review": (data_review, show_review),
    "today": (data_today, show_today),
    "progress": (data_progress, show_progress),
    "drill": (data_drill, show_drill),
}


def run(args) -> None:
    data, show = COMMANDS[args.cmd]
    if args.cmd == "drill" and not args.json and not args.answer:
        # The interactive deck needs no presented cards up front (presenting asks Stockfish per card)
        return drill_loop(args)
    if args.json:
        # The pipeline scripts print progress; with --json stdout carries only the result
        with contextlib.redirect_stdout(sys.stderr):
            result = data(args)
        return emit(result)
    show(data(args), args)


def build_parser() -> Parser:
    p = Parser(prog="teachess", description="Tu entrenador de ajedrez sobre tus partidas de chess.com.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, help_, fn=run):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--json", action="store_true", help="salida JSON para scripts y agentes")
        sp.set_defaults(fn=fn)
        return sp

    add("sync", "descarga partidas nuevas, las pasa por Stockfish y rehace el informe")
    sp = add("games", "lista tus partidas")
    sp.add_argument("--since", type=parse_since, default="30d",
                    help="30d, 2w, 3m o una fecha AAAA-MM-DD (por defecto 30d)")
    sp.add_argument("--tc", choices=[*core.TIME_CLASSES, "all"], help="ritmo (por defecto, el que analizas)")
    sp.add_argument("--limit", type=int, default=20)
    sp = add("openings", "tu repertorio con el %% de puntos por apertura")
    sp.add_argument("--color", default="all", choices=["white", "black", "all"])
    sp = add("review", "Stockfish sobre una partida: dónde se te fue")
    sp.add_argument("game", nargs="?", default="last", help="URL o número de partida, o «last» (por defecto)")
    sp = add("today", "cómo vas hoy y si deberías parar")
    sp.add_argument("--at", type=parse_at, help="otro momento, p. ej. 2026-10-15T02:47 (hora de Madrid)")
    sp = add("progress", "tu evolución mes a mes: elo, puntos y cuándo llega el primer error")
    sp.add_argument("--months", type=int, default=6)
    sp = add("drill", "repasa tus errores repetidos (con --json: las tarjetas de hoy, para un agente)")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--answer", nargs=2, metavar=("CARD", "MOVE"), help="responde a una tarjeta: su id y la jugada")
    sp = add("dash", "abre el panel en localhost", cmd_dash)
    sp.add_argument("--port", type=int, default=4747)
    sp.add_argument("--no-open", action="store_true", help="no abrir el navegador")
    sp.add_argument("--offline", action="store_true", help="no consultar chess.com")
    sp.add_argument("--at", type=parse_at, help="revive un momento pasado, p. ej. 2026-10-15T02:47 (sin conexión)")
    sp = add("setup", "elige cuánto historial analizar (con opciones según tus partidas) y lo analiza", cmd_setup)
    sp.add_argument("--window", choices=list(core.WINDOWS), help="3m, 6m, 1y, 2y, 3y o all; sin ella, te pregunta")
    sp.add_argument("--time-class", choices=list(core.TIME_CLASSES), help="ritmo; por defecto, el que más juegas")
    sp.add_argument("--yes", action="store_true", help="sin preguntar: la ventana recomendada")
    sp = add("watch", "te avisa al acabar cada partida y cuando toca parar; «install» lo deja de fondo", cmd_watch)
    sp.add_argument("action", nargs="?", choices=["install", "uninstall", "status"])
    sp.add_argument("--once", action="store_true", help="una sola revisión y sale (lo que ejecuta el servicio)")
    sp.add_argument("--offline", action="store_true", help="no consultar chess.com")
    sp.add_argument("--at", type=parse_at, help="revive un momento pasado, p. ej. 2026-10-15T02:47 (una revisión)")
    sp = add("skill", "la guía de teachess para agentes; «install» la instala en Claude Code", cmd_skill)
    sp.add_argument("action", nargs="?", choices=["install"])
    sp.add_argument("--dir", help="carpeta de skills (por defecto ~/.claude/skills)")
    sp = add("coach", "crea tu carpeta de entrenador y abre Claude Code en ella", cmd_coach)
    sp.add_argument("prompt", nargs="*", help="lo primero que le dices, p. ej. «¿cómo voy este mes?»")
    sp.add_argument("--dir", help="la carpeta (por defecto ~/teachess-coach)")
    sp.add_argument("--no-launch", action="store_true", help="solo prepara la carpeta")
    sp = add("mcp", "servidor MCP por stdio; «install» lo registra en Claude", cmd_mcp)
    sp.add_argument("action", nargs="?", choices=["install"])
    sp.add_argument("target", nargs="?", choices=["claude-code", "claude-desktop"])
    return p


def fail(e: TeachessError, as_json: bool) -> None:
    if as_json:
        print(json.dumps(e.envelope(), ensure_ascii=False), file=sys.stderr)
    else:
        print(getattr(e, "usage", "") + f"teachess: {e}", file=sys.stderr)
    sys.exit(e.exit)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    try:
        args = build_parser().parse_args(argv)
        args.fn(args)
    except TeachessError as e:
        fail(e, "--json" in argv)
    except KeyboardInterrupt:
        print()
        sys.exit(130)
