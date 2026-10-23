"""Leitner flashcards built from the repeated opening mistakes in data/report.json.

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
_choices_cache: dict[str, list[str]] = {}


def card_id(d: dict) -> str:
    return hashlib.sha1(f"{d['fen']}|{d['played']}".encode()).hexdigest()[:10]


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=1))


def is_due(entry: dict | None, today: date) -> bool:
    return entry is None or date.fromisoformat(entry["due"]) <= today


def deck(report: dict | None = None) -> list[dict]:
    report = core.load_report() if report is None else report
    return [{**d, "id": card_id(d), "n": i + 1} for i, d in enumerate(report.get("drills", []))]


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
    if card["id"] in _choices_cache:
        return _choices_cache[card["id"]]
    board = chess.Board(card["fen"])
    picks = [card["best"], card["played"]]
    path = shutil.which("stockfish")
    if path:
        engine = chess.engine.SimpleEngine.popen_uci(path)
        try:
            for info in engine.analyse(board, chess.engine.Limit(depth=10), multipv=4):
                san = board.san(info["pv"][0])
                if san not in picks:
                    picks.append(san)
                    break
        finally:
            engine.quit()
    if len(picks) < 3:
        picks.append(next(board.san(m) for m in board.legal_moves if board.san(m) not in picks))
    random.Random(card["id"]).shuffle(picks)
    _choices_cache[card["id"]] = picks
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
        "box": entry["box"] if entry else None, "example": card["example"],
    }


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
        if ev >= 30:
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
        if san == card["played"]:
            verdict = {"tone": "bad", "title": "Esta es la que sueles jugar",
                       "body": f"{label}{san} la has jugado {card['times']} de {card['reached']} veces y pierde "
                               f"{core.pawns(card['loss'])} peones. La buena es {label}{card['best']}. Vuelve mañana."}
        else:
            verdict = {"tone": "bad", "title": "No es la mejor",
                       "body": f"La que pide Stockfish es {label}{card['best']}. Vuelve mañana."}
    entry["due"] = (today + timedelta(days=days)).isoformat()
    state[card["id"]] = entry
    save_state(state)
    return {"correct": correct, "best": card["best"], "box": entry["box"], "due": entry["due"], **verdict}


def find(card_id_: str, report: dict | None = None) -> dict | None:
    return next((c for c in deck(report) if c["id"] == card_id_), None)
