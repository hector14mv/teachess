"""The agent side of teachess: the skill that teaches Claude Code the CLI, and the coach folder.

The coach folder is a plain directory with a CLAUDE.md (the coach's brief), notas.md (the training journal the
coach appends to after each conversation) and project settings that let it run teachess without asking.
Running `claude` inside it gives you a coach that remembers what you worked on last time.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

from . import core

TEMPLATES = Path(__file__).parent / "templates"
STATUS_TEXT = {"created": "creado", "updated": "actualizado", "unchanged": "sin cambios", "kept": "ya existe"}
FIRST_PROMPT = "¿Cómo voy?"
SETTINGS = {"permissions": {"allow": ["Bash(teachess:*)", "mcp__teachess", "Edit(./notas.md)"]}}


def skill_text() -> str:
    return (TEMPLATES / "SKILL.md").read_text()


def write(path: Path, text: str, overwrite: bool) -> str:
    """Write a file unless it already says this; with overwrite=False an edited file is left alone."""
    if path.exists():
        if path.read_text() == text:
            return "unchanged"
        if not overwrite:
            return "kept"
        status = "updated"
    else:
        status = "created"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return status


def install_skill(skills_dir: str | None = None) -> dict:
    base = Path(skills_dir).expanduser() if skills_dir else Path.home() / ".claude" / "skills"
    path = base / "teachess" / "SKILL.md"
    return {"status": write(path, skill_text(), overwrite=True), "path": str(path),
            "on_path": shutil.which("teachess") is not None}


def seed_notes() -> str:
    """The journal's first page: the plan from config.json and today's numbers as the starting point."""
    lines = ["# Diario de entrenamiento", "",
             "El entrenador añade una entrada fechada al final de cada conversación. Las anteriores no se tocan.", ""]
    advice = core.config.load().get("advice", {})
    if advice:
        lines += ["## Plan", ""]
        for k, a in advice.items():
            color, _, line = k.partition("|")
            lines.append(f"- Con {core.SIDE.get(color, color)}, tras {core.format_line(line.split())}: {a['text']}")
        lines.append("")
    report = core.load_report()
    if report and core.rating_now(core.load_games())["now"] is not None:
        p = core.progress(report=report, months=1)
        y, r = p["year"], p["rating"]
        lines += [f"## {date.today().isoformat()} · Punto de partida", "",
                  f"- Elo {core.tc()} {r['now']} (mejor {r['best']}, {r['best_date']}).",
                  f"- Último año: {y['games']} partidas, {core.fmt_pct(y['score'])} de los puntos.",
                  f"- Error serio antes de la jugada 12 en el {y['mistake_by12']} % de las partidas"
                  + ("." if y["median_first_mistake"] is None
                     else f"; el primero llega hacia la jugada {core.move_num(y['median_first_mistake'])}."),
                  f"- Tras perder sacas el {core.fmt_pct(y['score_after_loss'])}; "
                  f"tras ganar, el {core.fmt_pct(y['score_after_win'])}.", ""]
    return "\n".join(lines)


def make_folder(folder: str | None = None) -> dict:
    root = Path(folder).expanduser() if folder else Path.home() / "teachess-coach"
    files = [
        # The brief and the journal are the user's once written; the skill always follows the installed CLI
        (root / "CLAUDE.md", (TEMPLATES / "CLAUDE.md").read_text(), False),
        (root / "notas.md", seed_notes(), False),
        (root / ".claude" / "skills" / "teachess" / "SKILL.md", skill_text(), True),
        (root / ".claude" / "settings.json", json.dumps(SETTINGS, indent=2) + "\n", False),
    ]
    out = []
    for path, text, overwrite in files:
        # The journal is never compared: it grows every conversation and only the first version is ours
        status = "kept" if path.name == "notas.md" and path.exists() else write(path, text, overwrite)
        out.append({"path": str(path), "status": status})
    return {"dir": str(root), "files": out}


def launch(folder: str, prompt: str = "", launch: bool = True) -> None:
    """Replace this process with Claude Code inside the coach folder, or say how to start it."""
    claude = shutil.which("claude")
    if launch and claude and sys.stdin.isatty():
        os.chdir(folder)
        os.execvp(claude, [claude, prompt or FIRST_PROMPT])
    if launch and not claude:
        print("No encuentro Claude Code (claude) en tu PATH: https://claude.com/claude-code")
    print(f"Tu entrenador está en {folder}. Para hablar con él:\n  cd {folder} && claude")
