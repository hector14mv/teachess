"""Turn data/games.jsonl + data/evals.json into data/report.json for the coach page.

Usage: .venv/bin/python build_report.py [since=YYYY-MM-DD] [time_class=rapid]
"""

from __future__ import annotations

import json
import math
import re
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import chess

ROOT = Path(__file__).parent
SINCE = "2025-10-15"
TIME_CLASS = "rapid"
PLIES = 24
MISTAKE_CP = 80  # centipawns lost by one move to count as an opening mistake
BLUNDER_DROP = 0.3  # as lichess: a move that throws away 0.3 of the winning chances (on a -1..1 scale)
PUZZLES = 40  # the most recent blunders become puzzle cards
SESSION_GAP_MIN = 45

ES = {
    "Ruy Lopez Opening": "Apertura Española",
    "Caro Kann Defense": "Defensa Caro-Kann",
    "Scotch Game": "Apertura Escocesa",
    "Scandinavian Defense": "Defensa Escandinava",
    "Philidor Defense": "Defensa Philidor",
    "Italian Game": "Apertura Italiana",
    "Giuoco Piano Game": "Giuoco Piano",
    "Sicilian Defense": "Defensa Siciliana",
    "Four Knights Game": "Cuatro Caballos",
    "Three Knights Opening": "Tres Caballos",
    "French Defense": "Defensa Francesa",
    "Owens Defense": "Defensa Owen",
    "Englund Gambit": "Gambito Englund",
    "Queens Pawn Opening": "Peón de Dama",
    "Kings Pawn Opening": "Peón de Rey",
    "Center Game": "Apertura del Centro",
    "Bishops Opening": "Apertura de Alfil",
    "Van t Kruijs Opening": "Apertura Van't Kruijs",
    "Petrovs Defense": "Defensa Petrov",
    "Vienna Game": "Partida Vienesa",
    "Kings Gambit": "Gambito de Rey",
    "Pirc Defense": "Defensa Pirc",
    "Alekhines Defense": "Defensa Alekhine",
    "Modern Defense": "Defensa Moderna",
    "London System": "Sistema Londres",
    "Ponziani Opening": "Apertura Ponziani",
    "Danish Gambit": "Gambito Danés",
    "Nimzowitsch Defense": "Defensa Nimzowitsch",
    "Queens Gambit": "Gambito de Dama",
    "Kings Indian": "India de Rey",
    "Damiano Defense": "Defensa Damiano",
    "Elephant Gambit": "Gambito Elefante",
    "Latvian Gambit": "Gambito Letón",
}


def family(name: str) -> str:
    for en, es in ES.items():
        if name.startswith(en):
            return es
    words = []
    for w in name.split():
        if re.search(r"\d", w):
            break
        words.append(w)
    return " ".join(words[:3]) or "Otra"


def key(board: chess.Board) -> str:
    return " ".join(board.fen().split()[:4])


def win_chances(cp: int) -> float:
    """Centipawns as winning chances from -1 to 1 (lichess's curve), so +900 -> +600 is not a blunder."""
    return 2 / (1 + math.exp(-0.00368208 * cp)) - 1


def blunders(g: dict, evals: dict) -> tuple[list[dict], bool]:
    """The player's moves anywhere in the game that threw away BLUNDER_DROP of their winning chances, and
    whether every position of the game had an evaluation (otherwise the list may be missing some)."""
    sign = 1 if g["color"] == "white" else -1
    mine = 0 if g["color"] == "white" else 1
    board = chess.Board()
    out, complete = [], key(board) in evals
    for i, san in enumerate(g["sans"]):
        before = key(board)
        board.push_san(san)
        after = key(board)
        complete = complete and after in evals
        if i % 2 != mine or before not in evals or after not in evals or not evals[before]["best"]:
            continue
        cb, ca = sign * evals[before]["cp"], sign * evals[after]["cp"]
        drop = win_chances(cb) - win_chances(ca)
        if drop >= BLUNDER_DROP and san != evals[before]["best"]:
            out.append({"ply": i, "fen": before + " 0 1", "played": san, "best": evals[before]["best"],
                        "before": cb, "after": ca, "loss": cb - ca, "drop": round(drop, 2),
                        "line": g["sans"][:i]})
    return out, complete


def pct(xs) -> float:
    return round(100 * sum(x["score"] for x in xs) / len(xs), 1) if xs else None


def main() -> None:
    games = [json.loads(l) for l in open(ROOT / "data" / "games.jsonl")]
    evals = json.loads((ROOT / "data" / "evals.json").read_text())
    rapid = [g for g in games if g["time_class"] == TIME_CLASS]
    rec = [g for g in rapid if g["end"] >= SINCE]

    # --- walk each recent game through the engine evaluations -----------------------------
    pos_moves = defaultdict(lambda: defaultdict(list))  # fen -> my san -> [game]
    pos_meta = {}
    first_mistake = []
    for g in rec:
        sign = 1 if g["color"] == "white" else -1
        my_parity = 0 if g["color"] == "white" else 1
        board = chess.Board()
        g["ev10"] = None
        g["first_mistake"] = None
        for i, san in enumerate(g["sans"][:PLIES]):
            before = key(board)
            mine = i % 2 == my_parity
            moves_so_far = list(g["sans"][:i])
            board.push_san(san)
            after = key(board)
            if i + 1 == 20 and after in evals:
                g["ev10"] = sign * evals[after]["cp"]
            if not mine or before not in evals or after not in evals:
                continue
            loss = sign * (evals[before]["cp"] - evals[after]["cp"])
            pos_moves[before][san].append((g, loss))
            pos_meta.setdefault(before, {"line": moves_so_far, "best": evals[before]["best"],
                                         "cp": sign * evals[before]["cp"], "color": g["color"]})
            if loss >= MISTAKE_CP and g["first_mistake"] is None:
                g["first_mistake"] = i // 2 + 1
        first_mistake.append(g["first_mistake"])

    # --- recurring mistakes: same position, same bad move, several games ------------------
    drills = []
    for fen, moves in pos_moves.items():
        for san, hits in moves.items():
            losses = [l for _, l in hits]
            meta = pos_meta[fen]
            if len(hits) < 3 or st.median(losses) < MISTAKE_CP or san == meta["best"]:
                continue
            gs = [g for g, _ in hits]
            total = sum(len(v) for v in moves.values())
            drills.append({
                "fen": fen + " 0 1",
                "color": gs[0]["color"],
                "line": meta["line"],
                "played": san,
                "times": len(hits),
                "reached": total,
                "loss": round(st.median(losses)),
                "best": meta["best"],
                "eval_best": meta["cp"],
                "score": pct(gs),
                "example": gs[-1]["url"],
                "opening": family(gs[-1]["opening"]),
                "weight": len(hits) * min(st.median(losses), 400),
            })
    drills.sort(key=lambda d: -d["weight"])
    drills = drills[:24]

    # --- blunders anywhere in the game: the most recent become puzzles ----------------------
    puzzles, seen, per_game = [], set(), []
    for g in reversed(rec):
        found, complete = blunders(g, evals)
        if complete:
            per_game.append(len(found))
        for b in found:
            if len(puzzles) < PUZZLES and (b["fen"], b["played"]) not in seen:
                seen.add((b["fen"], b["played"]))
                puzzles.append({
                    "kind": "puzzle", "fen": b["fen"], "color": g["color"], "line": b["line"],
                    "played": b["played"], "best": b["best"], "times": 1, "reached": 1,
                    "loss": b["loss"], "eval_best": b["before"], "drop": b["drop"], "score": g["score"],
                    "example": g["url"], "end": g["end"], "opp": g["opp"],
                    "opening": family(g["opening"]) if g["opening"] else "Sin apertura",
                })

    # --- my own consistency: positions I reach often, how many different moves I play -----
    consistency = []
    for fen, moves in pos_moves.items():
        total = sum(len(v) for v in moves.values())
        if total < 15:
            continue
        ranked = sorted(moves.items(), key=lambda kv: -len(kv[1]))
        meta = pos_meta[fen]
        consistency.append({
            "line": meta["line"],
            "color": meta["color"],
            "reached": total,
            "best": meta["best"],
            "moves": [{"san": s, "n": len(h), "score": pct([g for g, _ in h]),
                       "loss": round(st.median([l for _, l in h]))} for s, h in ranked[:5]],
        })
    consistency.sort(key=lambda c: -c["reached"])

    # --- repertoire tree (first 10 plies) --------------------------------------------------
    def tree(color: str) -> dict:
        root = {"san": "", "n": 0, "pts": 0.0, "ev": [], "kids": {}}
        for g in rec:
            if g["color"] != color:
                continue
            node = root
            node["n"] += 1
            node["pts"] += g["score"]
            board = chess.Board()
            for san in g["sans"][:10]:
                board.push_san(san)
                node = node["kids"].setdefault(san, {"san": san, "n": 0, "pts": 0.0, "ev": [], "kids": {}})
                node["n"] += 1
                node["pts"] += g["score"]
                k = key(board)
                if k in evals:
                    node["ev"].append((1 if color == "white" else -1) * evals[k]["cp"])

        def prune(node: dict) -> dict:
            kids = [prune(k) for k in node["kids"].values() if k["n"] >= 4]
            kids.sort(key=lambda k: -k["n"])
            return {"s": node["san"], "n": node["n"], "p": round(100 * node["pts"] / node["n"]),
                    "e": round(st.median(node["ev"])) if node["ev"] else None, "k": kids}

        return prune(root)

    # --- openings table ----------------------------------------------------------------------
    by_open = defaultdict(list)
    for g in rec:
        by_open[(g["color"], family(g["opening"]))].append(g)
    openings = []
    for (color, fam), gs in by_open.items():
        if len(gs) < 12:
            continue
        ev = [g["ev10"] for g in gs if g["ev10"] is not None]
        fm = [g["first_mistake"] for g in gs if g["first_mistake"]]
        openings.append({
            "color": color, "name": fam, "n": len(gs), "score": pct(gs),
            "ev10": round(st.median(ev)) if ev else None,
            "short_losses": round(100 * sum(1 for g in gs if g["score"] == 0 and g["plies"] <= 50) / len(gs)),
            "mistake_by12": round(100 * len(fm) / len(gs)),
        })
    openings.sort(key=lambda o: -o["n"])

    # --- habits ------------------------------------------------------------------------------
    def dt(g):
        return datetime.fromisoformat(g["end"])

    after = defaultdict(list)
    for p, c in zip(rec, rec[1:]):
        gap = (dt(c) - dt(p)).total_seconds() / 60
        if gap >= SESSION_GAP_MIN:
            after["primera de la sesión"].append(c)
        else:
            after["tras ganar" if p["score"] == 1 else "tras perder" if p["score"] == 0 else "tras tablas"].append(c)
    after_all = defaultdict(list)
    for p, c in zip(rapid, rapid[1:]):
        gap = (dt(c) - dt(p)).total_seconds() / 60
        if gap < SESSION_GAP_MIN and p["score"] != 0.5:
            after_all["tras ganar" if p["score"] == 1 else "tras perder"].append(c)

    def band(h):
        return "madrugada" if h < 8 else "mañana" if h < 14 else "tarde" if h < 20 else "noche"

    by_band = defaultdict(list)
    by_band_all = defaultdict(list)
    for g in rec:
        by_band[band(dt(g).hour)].append(g)
    for g in rapid:
        by_band_all[band(dt(g).hour)].append(g)

    losses = [g for g in rec if g["score"] == 0 and g["my_clocks"]]
    wins = [g for g in rec if g["score"] == 1 and g["my_clocks"]]
    clock_left_losses = sorted(round(g["my_clocks"][-1]) for g in losses)

    def phase_used(a, b):
        xs = []
        for g in rec:
            c = [600] + g["my_clocks"]
            if len(c) > b:
                xs.append(c[a] - c[b])
        return round(st.median(xs)) if xs else None

    def castle_move(g):
        me = 0 if g["color"] == "white" else 1
        for i, s in enumerate(g["sans"]):
            if i % 2 == me and s.startswith("O-O"):
                return i // 2 + 1
        return None

    longish = [g for g in rec if g["plies"] >= 30]
    castled = [g for g in longish if castle_move(g)]
    uncastled = [g for g in longish if not castle_move(g)]

    terminations = {}
    for lab, v in [("win", 1), ("loss", 0)]:
        c = Counter(g["how"] for g in rec if g["score"] == v)
        terminations[lab] = dict(c.most_common())

    monthly = {}
    for g in rapid:
        monthly[g["end"][:7]] = g["my_rating"]

    acc = [g for g in rec if g["my_acc"]]
    fm = [x for x in first_mistake if x]

    report = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "since": SINCE,
        "totals": {
            "all_games": len(games), "rapid": len(rapid), "recent": len(rec),
            "recent_score": pct(rec),
            "white": {"n": sum(1 for g in rec if g["color"] == "white"),
                      "score": pct([g for g in rec if g["color"] == "white"])},
            "black": {"n": sum(1 for g in rec if g["color"] == "black"),
                      "score": pct([g for g in rec if g["color"] == "black"])},
            "rating_now": rapid[-1]["my_rating"],
            "first_e4_share": round(100 * sum(1 for g in rec if g["color"] == "white" and g["sans"][:1] == ["e4"])
                                    / max(1, sum(1 for g in rec if g["color"] == "white"))),
            "e5_share_vs_e4": round(100 * sum(1 for g in rec if g["color"] == "black" and g["sans"][:2] == ["e4", "e5"])
                                    / max(1, sum(1 for g in rec if g["color"] == "black" and g["sans"][:1] == ["e4"]))),
            "e5_share_vs_d4": round(100 * sum(1 for g in rec if g["color"] == "black" and g["sans"][:2] == ["d4", "e5"])
                                    / max(1, sum(1 for g in rec if g["color"] == "black" and g["sans"][:1] == ["d4"]))),
        },
        "monthly_rating": monthly,
        "first_mistake": {
            "share_by_move12": round(100 * len(fm) / len(rec)),
            "median_move": st.median(fm) if fm else None,
            "hist": dict(sorted(Counter(fm).items())),
        },
        "drills": drills,
        "puzzles": puzzles,
        "blunders": {"games": len(per_game),
                     "per_game": round(st.mean(per_game), 2) if per_game else None,
                     "clean_share": round(100 * sum(1 for n in per_game if n == 0) / len(per_game)) if per_game else None},
        "consistency": consistency[:14],
        "tree": {"white": tree("white"), "black": tree("black")},
        "openings": openings,
        "habits": {
            "after": {k: {"n": len(v), "score": pct(v)} for k, v in after.items()},
            "after_all": {k: {"n": len(v), "score": pct(v)} for k, v in after_all.items()},
            "band": {k: {"n": len(v), "score": pct(v)} for k, v in by_band.items()},
            "band_all": {k: {"n": len(v), "score": pct(v)} for k, v in by_band_all.items()},
            "clock_left_losses": clock_left_losses,
            "clock_left_median": {"loss": round(st.median(g["my_clocks"][-1] for g in losses)),
                                  "win": round(st.median(g["my_clocks"][-1] for g in wins))},
            "losses_over_3min_left": round(100 * sum(1 for x in clock_left_losses if x >= 180) / len(clock_left_losses)),
            "phase_seconds": {"1-10": phase_used(0, 10), "11-20": phase_used(10, 20),
                              "21-30": phase_used(20, 30), "31-40": phase_used(30, 40)},
            "castled": {"n": len(castled), "score": pct(castled),
                        "median_move": st.median(castle_move(g) for g in castled)},
            "uncastled": {"n": len(uncastled), "score": pct(uncastled)},
            "terminations": terminations,
            "short_loss_share": round(100 * sum(1 for g in rec if g["score"] == 0 and g["plies"] <= 50)
                                      / sum(1 for g in rec if g["score"] == 0)),
            "accuracy": {"n": len(acc), "mean": round(st.mean(g["my_acc"] for g in acc), 1),
                         "win": round(st.mean(g["my_acc"] for g in acc if g["score"] == 1), 1),
                         "loss": round(st.mean(g["my_acc"] for g in acc if g["score"] == 0), 1)},
        },
    }
    (ROOT / "data" / "report.json").write_text(json.dumps(report, ensure_ascii=False))
    print(f"report.json: {len(drills)} drills, {len(puzzles)} puzzles, {len(openings)} openings, "
          f"{len(consistency)} hub positions")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        SINCE = sys.argv[1]
    if len(sys.argv) > 2:
        TIME_CLASS = sys.argv[2]
    main()
