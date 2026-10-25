"""Progress bars for the long steps (downloading the archive, Stockfish over thousands of positions).

On a terminal the bar redraws in place: `Analizando tus partidas  ██████░░░░  62 %  · quedan ~2 min`, fitted to
the terminal's width (a line that wraps can no longer be redrawn, and each update would land on a new line).
Elsewhere (the watcher's log, a pipe) it writes a plain line every 25 %. With --json it stays silent.
"""

from __future__ import annotations

import os
import sys
import time
import unicodedata

QUIET = False  # set by --json: agents read the result, not the bar
WIDTH = 24
MIN_WIDTH = 8


def remaining(seconds: float) -> str:
    if seconds < 60:
        return "quedan unos segundos"
    return f"quedan ~{round(seconds / 60)} min"


def cells(text: str) -> int:
    """Columns `text` takes on a terminal: wide characters (CJK, most emoji) take two."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def clip(text: str, room: int) -> str:
    out = ""
    for c in text:
        if cells(out + c) > room:
            break
        out += c
    return out


def render(label: str, done: int, total: int, notes: list[str], columns: int) -> str:
    """One line of the bar that fits in `columns` (the last column stays free: some terminals wrap on it).
    The bar shrinks first, then the notes go (the last one first), then the label is shortened."""
    room = max(1, columns - 1)
    pct = f"{int(100 * done / total):3d} %"
    notes = list(notes)
    while True:
        tail = "".join(f"  · {n}" for n in notes)
        width = room - cells(label) - len(pct) - cells(tail) - 4
        if width >= MIN_WIDTH or not notes:
            break
        notes.pop()
    if width < MIN_WIDTH:  # too narrow even bare: shorten the label, keep the bar and the percentage
        label = clip(label, max(0, cells(label) - (MIN_WIDTH - width) - 1)) + "…"
    width = max(MIN_WIDTH, min(WIDTH, width))
    filled = round(width * done / total)
    return clip(f"{label}  {'█' * filled}{'░' * (width - filled)}  {pct}{tail}", room)


def columns(stream) -> int:
    try:
        cols = os.get_terminal_size(stream.fileno()).columns
    except (OSError, ValueError, AttributeError):
        cols = 0
    return cols or 80  # a terminal that has not told its size yet reports 0


class Bar:
    def __init__(self, label: str, total: int, unit: str = "", stream=None):
        self.label, self.total, self.unit = label, max(1, total), unit
        self.stream = stream or sys.stderr
        self.live = self.stream.isatty()
        self.start = time.monotonic()
        self.done = 0
        self.shown = -1  # the last percentage drawn on a terminal
        self.quarter = 0  # the last quarter written elsewhere

    def update(self, done: int) -> None:
        self.done = min(done, self.total)
        if QUIET:
            return
        pct = int(100 * self.done / self.total)
        if self.live:
            if pct == self.shown and self.done != self.total:
                return
            self.shown = pct
            notes = []  # by importance: what is left, then the count
            elapsed = time.monotonic() - self.start
            if 0 < self.done < self.total and elapsed > 3:
                notes.append(remaining(elapsed / self.done * (self.total - self.done)))
            if self.unit:
                notes.append(f"{self.done:,}/{self.total:,} {self.unit}".replace(",", "."))
            line = render(self.label, self.done, self.total, notes, columns(self.stream))
            self.stream.write("\r" + line + "\033[K")
            self.stream.flush()
        elif pct // 25 > self.quarter:
            self.quarter = pct // 25
            self.stream.write(f"{self.label}: {pct} %\n")
            self.stream.flush()

    def step(self, n: int = 1) -> None:
        self.update(self.done + n)

    def close(self) -> None:
        if not QUIET and self.live and self.shown >= 0:
            self.update(self.total)
            self.stream.write("\n")
            self.stream.flush()
