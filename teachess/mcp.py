"""A Model Context Protocol server over stdio, so Claude (Code or Desktop) can read your games as tools.

Each tool runs the CLI command it is named after through the same parser and data function, and returns the
same JSON that `teachess <command> --json` prints. A failing command comes back as a tool error whose text is
the error envelope ({"error": {"code", "exit", "message"}}). Standard library only: newline-delimited
JSON-RPC 2.0 on stdin/stdout, the stdio transport of the MCP spec.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

from . import cli, core
from .errors import SetupError, TeachessError, UsageError

BIN = core.ROOT / "bin" / "teachess"
STATUS_TEXT = {"created": "instalado", "updated": "actualizado", "unchanged": "ya estaba instalado"}
INSTRUCTIONS = """\
teachess reads the user's own chess.com games, evaluated by Stockfish over the first 12 moves of every rapid
game of the last year. Text fields (advice, verdict, title, body, opening names) are in Spanish: quote or
translate them. `score` is the share of points (win 1, draw 1/2). A serious mistake loses 0.8 pawns or more.
Ground every claim in a number from a tool. If today.state is "streak", tell the user to stop for 45 minutes
first. To quiz: show one card from `drill` (its line and position) with its three choices without saying which
is best, then call `drill_answer` with the move the user picked; never answer for them."""


def string(desc: str, enum: list[str] | None = None) -> dict:
    return {"type": "string", "description": desc, **({"enum": enum} if enum else {})}


def integer(desc: str) -> dict:
    return {"type": "integer", "minimum": 1, "description": desc}


def flags(a: dict, *names: str) -> list[str]:
    out = []
    for n in names:
        if a.get(n) is not None:
            out += [f"--{n}", str(a[n])]
    return out


# name -> (description, input properties, required, CLI argv built from the arguments)
TOOLS = {
    "today": ("Where the player stands right now: current session, losing streak, whether to stop, last game.",
              {"at": string("Another moment, ISO date-time in Madrid time, e.g. 2026-10-15T02:47")}, [],
              lambda a: ["today", *flags(a, "at")]),
    "progress": ("Month by month in rapid: games, share of points, rating, % of games with a serious mistake "
                 "before move 12 (lower is better) and the median move of the first one (later is better); plus the yearly baseline and drill counters.",
                 {"months": integer("How many months back (default 6)")}, [],
                 lambda a: ["progress", *flags(a, "months")]),
    "games": ("The player's games, newest first.",
              {"since": string("30d, 2w, 3m or a date YYYY-MM-DD (default 30d)"),
               "tc": string("Time control (default rapid)", [*core.TIME_CLASSES, "all"]),
               "limit": integer("Maximum games (default 20)")}, [],
              lambda a: ["games", *flags(a, "since", "tc", "limit")]),
    "openings": ("The repertoire of the last year: games, share of points and % with a mistake before move 12.",
                 {"color": string("Which side (default all)", ["white", "black", "all"])}, [],
                 lambda a: ["openings", *flags(a, "color")]),
    "review": ("Stockfish over one game's opening: every move that lost 0.8 pawns or more, the better move, "
               "and whether it is a mistake the player keeps repeating.",
               {"game": string("'last' (default), a chess.com game URL or its number")}, [],
               lambda a: ["review", "--", a.get("game") or "last"]),
    "sync": ("Fetch new games from chess.com, evaluate them with Stockfish and rebuild the report. Returns the "
             "new games. Seconds usually; minutes on the very first run.", {}, [], lambda a: ["sync"]),
    "drill": ("Flashcards due today from the player's repeated opening mistakes: position (fen), the line that "
              "led there and three candidate moves.",
              {"limit": integer("Maximum cards (default 10)")}, [], lambda a: ["drill", *flags(a, "limit")]),
    "drill_answer": ("Grade the move the player chose for a flashcard and schedule the card again. Changes the "
                     "saved progress: only call it with the player's own choice.",
                     {"card": string("The card id from drill"), "move": string("One of the card's choices[].san")},
                     ["card", "move"], lambda a: ["drill", "--answer", a["card"], a["move"]]),
}


def tool_list() -> list[dict]:
    return [{"name": name, "description": desc,
             "inputSchema": {"type": "object", "properties": props, "required": req, "additionalProperties": False}}
            for name, (desc, props, req, _) in TOOLS.items()]


def call(name: str, arguments: dict) -> dict:
    def text(data, error=False):
        return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}],
                "isError": error}

    try:
        if name not in TOOLS:
            raise UsageError(f"No existe la herramienta «{name}».")
        _, props, required, argv = TOOLS[name]
        unknown = set(arguments) - set(props)
        missing = [k for k in required if k not in arguments]
        if unknown or missing:
            parts = ([f"no conozco {', '.join(sorted(unknown))}"] if unknown else []) + \
                    ([f"falta {', '.join(missing)}"] if missing else [])
            raise UsageError(f"Argumentos no válidos para {name}: {'; '.join(parts)}.")
        cmd, *rest = argv(arguments)
        # --json goes before the rest: review ends with "--" so a game id can never be read as an option
        args = cli.build_parser().parse_args([cmd, "--json", *rest])
        return text(cli.COMMANDS[args.cmd][0](args))
    except TeachessError as e:
        return text(e.envelope(), error=True)
    except SystemExit:  # argparse's --help and friends must not stop the server
        return text(UsageError(f"Argumentos no válidos para {name}.").envelope(), error=True)
    except Exception as e:  # a tool failure must not take the server down
        traceback.print_exc()
        return text({"error": {"code": "error", "exit": 1, "message": f"{type(e).__name__}: {e}"}}, error=True)


def handle(msg: dict) -> dict | None:
    method, params = msg.get("method"), msg.get("params") or {}
    if method is None or "id" not in msg:
        return None  # notifications (initialized, cancelled) and replies to us need no answer
    if method == "initialize":
        # Tools and text content have not changed across protocol versions, so speak the client's
        result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "teachess", "version": "0.2.0"},
                  "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_list()}
    elif method == "tools/call":
        result = call(params.get("name", ""), params.get("arguments") or {})
    else:
        return {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": msg["id"], "result": result}


def serve() -> None:
    sys.stdout.flush()
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8")
    # From here on fd 1 is stderr, so progress lines from the pipeline scripts and the Stockfish batch workers
    # can never corrupt the protocol stream.
    os.dup2(2, 1)
    if not shutil.which("stockfish"):
        # Claude Desktop starts servers with a bare PATH; Homebrew's Stockfish lives here
        os.environ["PATH"] += os.pathsep + os.pathsep.join(["/opt/homebrew/bin", "/usr/local/bin"])

    def send(reply) -> None:
        proto.write(json.dumps(reply, ensure_ascii=False) + "\n")
        proto.flush()

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        if isinstance(msg, list):
            replies = [r for r in map(handle, msg) if r]
            if replies:
                send(replies)
        else:
            reply = handle(msg)
            if reply:
                send(reply)


# --- install ----------------------------------------------------------------------------------

def install(target: str) -> dict:
    return install_claude_code() if target == "claude-code" else install_claude_desktop()


def install_claude_code() -> dict:
    """Register the server at user scope through Claude Code's own CLI, which owns ~/.claude.json."""
    claude = shutil.which("claude")
    if not claude:
        raise SetupError("No encuentro Claude Code (claude) en tu PATH: https://claude.com/claude-code")
    config = Path.home() / ".claude.json"
    try:
        current = json.loads(config.read_text()).get("mcpServers", {}).get("teachess")
    except (OSError, ValueError):
        current = None
    if current and current.get("command") == str(BIN) and current.get("args") == ["mcp"]:
        return {"status": "unchanged", "config": str(config)}

    def run(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([claude, "mcp", *argv], capture_output=True, text=True)

    if current:
        run("remove", "teachess", "--scope", "user")
    done = run("add", "--scope", "user", "teachess", "--", str(BIN), "mcp")
    if done.returncode:
        raise SetupError(f"claude mcp add falló: {(done.stderr or done.stdout).strip()}")
    return {"status": "updated" if current else "created", "config": str(config),
            "note": "Ya está en todas tus sesiones nuevas de Claude Code (compruébalo con /mcp)."}


def desktop_config() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def install_claude_desktop() -> dict:
    path = desktop_config()
    try:
        cfg = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        raise SetupError(f"{path} no es JSON válido; no lo toco.") from None
    entry = {"command": str(BIN), "args": ["mcp"]}
    servers = cfg.setdefault("mcpServers", {})
    if servers.get("teachess") == entry:
        return {"status": "unchanged", "config": str(path)}
    status = "updated" if "teachess" in servers else "created"
    servers["teachess"] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return {"status": status, "config": str(path), "note": "Reinicia Claude Desktop para que lo cargue."}
