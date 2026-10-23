"""Leitner flashcards from data/report.json: repeated opening mistakes, then puzzles from recent blunders.

Progress lives in data/drill_state.json so the CLI and the dashboard share it.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
from datetime import date, timedelta

import chess
import chess.engine

from . import core

STATE = core.DATA / "drill_state.json"
INTERVALS = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30}  # box -> days until the card comes back
_choices_cache: dict[str, list[str]] = {}  # card id|answer -> choices


def card_id(d: dict) -> str:
    # Opening cards keep their original ids (saved progress); puzzles get their own namespace
    prefix = "puzzle|" if d.get("kind") == "puzzle" else ""
    return hashlib.sha1(f"{prefix}{d['fen']}|{d['played']}".encode()).hexdigest()[:10]


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=1))


def is_due(entry: dict | None, today: date) -> bool:
    return entry is None or date.fromisoformat(entry["due"]) <= today


def plan() -> dict:
    """The coach's chosen moves from config.json's advice, keyed by colour and line ("black|d4")."""
    return {k: a for k, a in core.config.load().get("advice", {}).items() if a.get("move")}


def legal(fen: str, san: str) -> bool:
    board = chess.Board(fen)
    try:
        move = board.parse_san(san)
    except ValueError:
        return False
    return move != chess.Move.null() and board.is_legal(move)  # parse_san also accepts "--", a null move


def deck(report: dict | None = None) -> list[dict]:
    """The repeated opening mistakes as cards, bent to the plan (a move the plan keeps is not drilled; where the
    plan picks another move than Stockfish, the card asks for the plan's move), then the blunder puzzles."""
    report = core.load_report() if report is None else report
    chosen = plan()
    cards = []
    for d in report.get("drills", []):
        a = chosen.get(f"{d['color']}|{' '.join(d['line'])}")
        if a and not legal(d["fen"], a["move"]):
            a = None  # a typo in the plan must not break the deck: drill Stockfish's move there
        if a and d["played"] == a["move"]:
            continue
        card = {**d, "id": card_id(d)}
        if a and a["move"] != d["best"]:
            card.update(best=a["move"], engine_best=d["best"], plan=a["text"])
        cards.append(card)
    cards += [{**d, "id": card_id(d)} for d in report.get("puzzles", [])]
    return [{**c, "n": i + 1} for i, c in enumerate(cards)]


def queue(report: dict | None = None, today: date | None = None) -> tuple[list[dict], dict]:
    """Cards due today in deck order (heaviest mistakes first) and the deck counters."""
    today = today or date.today()
    cards = deck(report)
    state = load_state()
    due = [c for c in cards if is_due(state.get(c["id"]), today)]
    boxes = [state[c["id"]]["box"] for c in cards if c["id"] in state]
    stats = {"total": len(cards), "due": len(due), "seen": len(boxes),
             "learned": sum(1 for b in boxes if b >= 4)}
    return due, stats


def choices(card: dict) -> list[str]:
    """The best move, the move the player keeps playing and one more engine candidate, shuffled."""
    # Keyed by the answer too: changing the plan changes the right move of a card that keeps its id
    cache_key = f"{card['id']}|{card['best']}|{card.get('engine_best')}"
    if cache_key in _choices_cache:
        return _choices_cache[cache_key]
    board = chess.Board(card["fen"])
    picks = [card["best"], card["played"]]
    path = shutil.which("stockfish")
    if path:
        engine = chess.engine.SimpleEngine.popen_uci(path)
        try:
            for info in engine.analyse(board, chess.engine.Limit(depth=10), multipv=4):
                san = board.san(info["pv"][0])
                # On a plan card, Stockfish's own favourite would be graded wrong: leave it out
                if san not in picks and san != card.get("engine_best"):
                    picks.append(san)
                    break
        finally:
            engine.quit()
    if len(picks) < 3:
        # Fewer than three moves to offer (a cramped position) is fine: the card shows two
        extra = next((board.san(m) for m in board.legal_moves
                      if board.san(m) not in picks and board.san(m) != card.get("engine_best")), None)
        if extra:
            picks.append(extra)
    random.Random(card["id"]).shuffle(picks)
    _choices_cache[cache_key] = picks
    return picks


def present(card: dict) -> dict:
    """A card ready to show: position, the line that led there and the three options."""
    board = chess.Board(card["fen"])
    ply = len(card["line"])
    label = core.move_label(ply)
    opts = []
    for san in choices(card):
        mv = board.parse_san(san)
        opts.append({"san": san, "label": label + san,
                     "from": chess.square_name(mv.from_square), "to": chess.square_name(mv.to_square)})
    entry = load_state().get(card["id"])
    return {
        "id": card["id"], "n": card["n"], "fen": card["fen"], "color": card["color"],
        "side": core.SIDE[card["color"]], "opening": card["opening"],
        "line": core.format_line(card["line"]), "ask": label + " ?",
        "times": card["times"], "reached": card["reached"], "choices": opts,
        "box": entry["box"] if entry else None, "example": card["example"], "plan": card.get("plan"),
        "kind": card.get("kind", "opening"), "context": context(card),
    }


def context(card: dict) -> str:
    """One line on where the card comes from."""
    if card.get("kind") != "puzzle":
        return f"Aquí te has equivocado {card['times']} de las {card['reached']} veces que has llegado."
    d = core.end_dt(card)
    return (f"De tu partida del {d.day} {core.MONTHS[d.month - 1]} contra {card['opp']}: "
            f"ibas {core.eval_text(card['eval_best'])}. Encuentra la buena.")


def answer(card: dict, san: str, today: date | None = None) -> dict:
    """Grade an answer, move the card between boxes and say why."""
    today = today or date.today()
    if san not in choices(card):
        raise ValueError(f"{san} no es una de las opciones")
    state = load_state()
    entry = state.get(card["id"], {"box": 0, "right": 0, "wrong": 0})
    label = core.move_label(len(card["line"]))
    correct = san == card["best"]
    if correct:
        entry["box"] = min(5, entry["box"] + 1)
        entry["right"] += 1
        days = INTERVALS[entry["box"]]
        ev = card["eval_best"]
        if card.get("plan"):
            why = "Es la jugada de tu plan."
        elif ev >= 30:
            why = f"Mantienes la ventaja (+{core.pawns(ev)} según Stockfish)."
        elif ev > -30:
            why = "Mantienes la posición igualada."
        else:
            why = f"Es lo mejor en una posición difícil (-{core.pawns(ev)})."
        verdict = {"tone": "good", "title": f"Correcto: {label}{san}",
                   "body": f"{why} Vuelve a salir dentro de {days} día{'s' if days > 1 else ''}."}
    else:
        entry["box"] = 1
        entry["wrong"] += 1
        days = INTERVALS[1]
        good = (f"La de tu plan es {label}{card['best']}" if card.get("plan")
                else f"La buena es {label}{card['best']}")
        if san == card["played"] and card.get("kind") == "puzzle":
            verdict = {"tone": "bad", "title": "Es la que jugaste en la partida",
                       "body": f"{label}{san} te costó {core.pawns(card['loss'])} peones. {good}. Vuelve mañana."}
        elif san == card["played"]:
            verdict = {"tone": "bad", "title": "Esta es la que sueles jugar",
                       "body": f"{label}{san} la has jugado {card['times']} de {card['reached']} veces y pierde "
                               f"{core.pawns(card['loss'])} peones. {good}. Vuelve mañana."}
        else:
            verdict = {"tone": "bad", "title": "No es la de tu plan" if card.get("plan") else "No es la mejor",
                       "body": (f"{good}. Vuelve mañana." if card.get("plan")
                                else f"La que pide Stockfish es {label}{card['best']}. Vuelve mañana.")}
    entry["due"] = (today + timedelta(days=days)).isoformat()
    state[card["id"]] = entry
    save_state(state)
    return {"correct": correct, "best": card["best"], "box": entry["box"], "due": entry["due"], **verdict}


def find(card_id_: str, report: dict | None = None) -> dict | None:
    return next((c for c in deck(report) if c["id"] == card_id_), None)
