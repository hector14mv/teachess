"""Progress bars for the long steps (downloading the archive, Stockfish over thousands of positions).

On a terminal the bar redraws in place: `Analizando tus partidas  ██████░░░░  62 %  · quedan ~2 min`.
Elsewhere (the watcher's log, a pipe) it writes a plain line every 25 %. With --json it stays silent.
"""

from __future__ import annotations

import sys
import time

QUIET = False  # set by --json: agents read the result, not the bar
WIDTH = 24


def remaining(seconds: float) -> str:
    if seconds < 60:
        return "quedan unos segundos"
    return f"quedan ~{round(seconds / 60)} min"


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
            filled = round(WIDTH * self.done / self.total)
            line = f"\r{self.label}  {'█' * filled}{'░' * (WIDTH - filled)}  {pct:3d} %"
            if self.unit:
                line += f"  · {self.done:,}/{self.total:,} {self.unit}".replace(",", ".")
            elapsed = time.monotonic() - self.start
            if 0 < self.done < self.total and elapsed > 3:
                line += f"  · {remaining(elapsed / self.done * (self.total - self.done))}"
            self.stream.write(line + "\033[K")
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
