"""Errors with a stable exit code, so scripts and agents can tell what went wrong without parsing text.

0 ok · 1 unexpected · 2 bad usage · 3 setup missing (username, data, a tool) · 4 not found
· 5 chess.com unreachable or rate-limiting. With --json the error is also printed to stderr as
{"error": {"code", "exit", "message"}}.
"""

from __future__ import annotations


class TeachessError(Exception):
    exit = 1
    code = "error"

    def envelope(self) -> dict:
        return {"error": {"code": self.code, "exit": self.exit, "message": str(self)}}


class UsageError(TeachessError):
    exit = 2
    code = "usage"


class SetupError(TeachessError):
    exit = 3
    code = "setup"


class NotFound(TeachessError):
    exit = 4
    code = "not_found"


class Unreachable(TeachessError):
    exit = 5
    code = "chess_com"
