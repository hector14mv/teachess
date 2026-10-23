"""Notifications: a macOS banner, and a push to your phone through ntfy when config.json names a topic.

config.json: {"notify": {"ntfy": "https://ntfy.sh/<your-secret-topic>"}}. Install the ntfy app and subscribe to
the same topic. send() never raises; it returns False when every channel it tried failed, so the watcher
can try again on its next check.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys
import urllib.request

from . import core


def send(title: str, body: str, url: str | None = None) -> bool:
    print(f"  ▸ {title} — {body}", file=sys.stderr, flush=True)  # stderr: stdout may carry --json
    tried = delivered = 0
    if sys.platform == "darwin" and shutil.which("osascript"):
        tried += 1
        # The text goes in as arguments, never into the script, so quotes in it cannot break out
        try:
            done = subprocess.run(["osascript", "-e", "on run argv", "-e",
                                   "display notification (item 2 of argv) with title \"teachess\" subtitle (item 1 of argv)",
                                   "-e", "end run", title, body], capture_output=True, timeout=10)
            delivered += done.returncode == 0
        except (OSError, subprocess.SubprocessError) as e:
            print(f"  ▸ sin aviso en el Mac: {e}", file=sys.stderr, flush=True)
    topic = (core.config.load().get("notify") or {}).get("ntfy")
    if topic:
        tried += 1
        # HTTP headers are ASCII: ntfy reads RFC 2047 encoded words for anything else
        headers = {"Title": f"=?UTF-8?B?{base64.b64encode(title.encode()).decode()}?=", "Tags": "chess_pawn"}
        if url:
            headers["Click"] = url
        try:
            urllib.request.urlopen(urllib.request.Request(topic, data=body.encode("utf-8"), headers=headers),
                                   timeout=10).close()
            delivered += 1
        except (OSError, ValueError) as e:  # unreachable, or not a URL at all
            print(f"  ▸ ntfy no responde: {e}", file=sys.stderr, flush=True)
    return delivered > 0 or not tried  # with no channel at all, the log line is the notification
