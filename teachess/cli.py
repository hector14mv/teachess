"""teachess: your chess.com games from the terminal. Every command takes --json for agents."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta

import chess

from . import core, drills

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


# --- commands ---------------------------------------------------------------------------------

def cmd_sync(args) -> None:
    log = (lambda *_: None) if args.json else print
    new = core.sync(log=log)
    if args.json:
        return emit([core.describe(g) for g in new])
    if not new:
        print("Sin partidas nuevas.")
    for g in new:
        d = core.describe(g)
        print(f"  {d['when']:<13} {result_text(d)} {d['side']:<8} {d['opening']}")
    if new:
        print(f"{len(new)} partida{'s' if len(new) > 1 else ''} nueva{'s' if len(new) > 1 else ''}. Informe actualizado.")


def cmd_games(args) -> None:
    since = parse_since(args.since)
    games = [g for g in core.load_games()
             if g["end"] >= since and (args.tc == "all" or g["time_class"] == args.tc)]
    rows = [core.describe(g) for g in games[-args.limit:]][::-1]
    if args.json:
        return emit(rows)
    if not rows:
        return print("Ninguna partida en ese periodo.")
    for d in rows:
        print(f"{dim(d['when']):<13}  {result_text(d)} {d['side']:<8} {d['opening'][:28]:<28} "
              f"{dim('vs ' + d['opp'] + ' (' + str(d['opp_rating']) + ')')}  {dim(d['how'])}")
    pts = sum(g["score"] for g in games)
    pts_txt = f"{pts:g}".replace(".", ",")
    print(dim(f"\n{len(games)} partidas desde {since}: {pts_txt} puntos ({100 * pts / len(games):.0f} %)."))


def cmd_openings(args) -> None:
    report = core.load_report()
    rows = [o for o in report.get("openings", []) if args.color in ("all", o["color"])]
    if args.json:
        return emit(rows)
    print(dim(f"Rapid desde {report.get('since')}  ·  % de puntos  ·  % con error antes de la jugada 12\n"))
    for o in rows:
        pct = f"{o['score']:.0f} %"
        pct = blue(pct) if o["score"] >= 52 else orange(pct) if o["score"] <= 47 else pct
        print(f"  {core.SIDE[o['color']]:<8} {o['name']:<24} {o['n']:>4} partidas  {pct:>6}   "
              f"{dim(str(o['mistake_by12']) + ' % con error')}")


def cmd_review(args) -> None:
    games = core.load_games()
    g = core.find_game(games, args.game)
    if not g:
        sys.exit(f"No encuentro la partida «{args.game}». Usa una URL de chess.com, su número o «last».")
    core.evaluate_games([g], log=(lambda *_: None) if args.json else print)
    r = core.review(g)
    d = core.describe(g)
    if args.json:
        return emit({**d, **r})
    print(f"{bold(d['when'])}  {result_text(d)} con {d['side']} vs {d['opp']} ({d['opp_rating']})  ·  {d['opening']}")
    print(f"{g['url']}\n")
    print(orange(r["verdict"]) if r["first"] else blue(r["verdict"]))
    for m in r["mistakes"]:
        rep = f"  · repetido {m['repeated']} de {m['reached']} veces" if m["repeated"] else ""
        print(f"  {m['move']:<9} pierde {core.pawns(m['loss'])}  ·  mejor {m['best_move']}{dim(rep)}")


def cmd_today(args) -> None:
    t = core.today(at=args.at)
    if args.json:
        return emit(t)
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


def cmd_drill(args) -> None:
    due, stats = drills.queue()
    if not due:
        return print(f"Nada pendiente hoy. {stats['learned']} de {stats['total']} tarjetas aprendidas.")
    print(dim(f"{stats['due']} tarjetas para hoy de {stats['total']}. Escribe 1, 2 o 3; q para salir.\n"))
    for card in due[:args.limit]:
        c = drills.present(card)
        board = chess.Board(c["fen"])
        flip = c["color"] == "black"
        print(bold(f"Tarjeta {c['n']} de {stats['total']} · {c['opening']} con {c['side']}"))
        print(dim(f"Aquí te has equivocado {c['times']} de las {c['reached']} veces que has llegado."))
        print(board.unicode(invert_color=True, borders=False, empty_square="·", orientation=not flip))
        print(dim(" ".join("hgfedcba" if flip else "abcdefgh")))
        print(f"\n{c['line']} — {bold(c['ask'])}")
        for i, ch in enumerate(c["choices"], 1):
            print(f"  {i}. {ch['label']}")
        pick = input("> ").strip().lower()
        while pick not in ("1", "2", "3", "q"):
            pick = input("1, 2, 3 o q > ").strip().lower()
        if pick == "q":
            break
        res = drills.answer(card, c["choices"][int(pick) - 1]["san"])
        print((blue if res["correct"] else orange)(bold(res["title"])))
        print(res["body"] + "\n")


def cmd_dash(args) -> None:
    from . import server
    server.serve(port=args.port, open_browser=not args.no_open, live=not args.offline, at=args.at)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="teachess", description="Tu entrenador de ajedrez sobre tus partidas de chess.com.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--json", action="store_true", help="salida JSON para scripts y agentes")
        sp.set_defaults(fn=fn)
        return sp

    add("sync", cmd_sync, "descarga partidas nuevas, las pasa por Stockfish y rehace el informe")
    sp = add("games", cmd_games, "lista tus partidas")
    sp.add_argument("--since", default="30d", help="30d, 2w, 3m o una fecha AAAA-MM-DD (por defecto 30d)")
    sp.add_argument("--tc", default="rapid", choices=[*core.TIME_CLASSES, "all"], help="ritmo (por defecto rapid)")
    sp.add_argument("--limit", type=int, default=20)
    sp = add("openings", cmd_openings, "tu repertorio con el %% de puntos por apertura")
    sp.add_argument("--color", default="all", choices=["white", "black", "all"])
    sp = add("review", cmd_review, "Stockfish sobre una partida: dónde se te fue")
    sp.add_argument("game", nargs="?", default="last", help="URL o número de partida, o «last» (por defecto)")
    sp = add("today", cmd_today, "cómo vas hoy y si deberías parar")
    sp.add_argument("--at", type=parse_at, help="otro momento, p. ej. 2026-10-15T02:47 (hora de Madrid)")
    sp = add("drill", cmd_drill, "repasa en la terminal tus errores repetidos")
    sp.add_argument("--limit", type=int, default=10)
    sp = add("dash", cmd_dash, "abre el panel en localhost")
    sp.add_argument("--port", type=int, default=4747)
    sp.add_argument("--no-open", action="store_true", help="no abrir el navegador")
    sp.add_argument("--offline", action="store_true", help="no consultar chess.com")
    sp.add_argument("--at", type=parse_at, help="revive un momento pasado, p. ej. 2026-10-15T02:47 (sin conexión)")

    args = p.parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        print()
