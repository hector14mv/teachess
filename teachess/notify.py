"""Notifications: a macOS banner, and a push to your phone through ntfy when config.json names a topic.

config.json: {"notify": {"ntfy": "https://ntfy.sh/<your-secret-topic>"}}. Install the ntfy app and subscribe to
the same topic. Every channel is best effort: a notification that cannot be shown never stops the watcher.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys
import urllib.request

from . import core


def send(title: str, body: str, url: str | None = None) -> None:
    print(f"  ▸ {title} — {body}", flush=True)
    if sys.platform == "darwin" and shutil.which("osascript"):
        # The text goes in as arguments, never into the script, so quotes in it cannot break out
        subprocess.run(["osascript", "-e", "on run argv", "-e",
                        "display notification (item 2 of argv) with title \"teachess\" subtitle (item 1 of argv)",
                        "-e", "end run", title, body], capture_output=True)
    topic = core.config.load().get("notify", {}).get("ntfy")
    if topic:
        # HTTP headers are ASCII: ntfy reads RFC 2047 encoded words for anything else
        headers = {"Title": f"=?UTF-8?B?{base64.b64encode(title.encode()).decode()}?=", "Tags": "chess_pawn"}
        if url:
            headers["Click"] = url
        try:
            urllib.request.urlopen(urllib.request.Request(topic, data=body.encode("utf-8"), headers=headers),
                                   timeout=10).close()
        except OSError as e:
            print(f"  ▸ ntfy no responde: {e}", file=sys.stderr, flush=True)
