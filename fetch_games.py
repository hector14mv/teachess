"""Download every monthly game archive of a chess.com player via the public PubAPI.

Usage: python3 fetch_games.py [username]   (default: TEACHESS_USER or config.json)
Writes data/raw/YYYY-MM.json (one file per month). Past months are cached;
the current month is always refetched. Requests are serial: chess.com rate-limits parallel calls.
"""

import json
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

import config

UA = "teachess/0.1 (personal chess coach)"
RAW = Path(__file__).parent / "data" / "raw"


def get(url: str) -> dict:
    for attempt in range(5):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"rate-limited too many times: {url}")


def main(user: str) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    current = date.today().strftime("%Y-%m")
    archives = get(f"https://api.chess.com/pub/player/{user}/games/archives")["archives"]
    total = 0
    for url in archives:
        yyyy, mm = url.rstrip("/").split("/")[-2:]
        out = RAW / f"{yyyy}-{mm}.json"
        if out.exists() and f"{yyyy}-{mm}" != current:
            total += len(json.loads(out.read_text())["games"])
            continue
        games = get(url)["games"]
        out.write_text(json.dumps({"games": games}))
        total += len(games)
        print(f"{yyyy}-{mm}: {len(games)} games", flush=True)
    print(f"total: {total} games in {len(archives)} months")


if __name__ == "__main__":
    main(sys.argv[1].lower() if len(sys.argv) > 1 else config.chess_user())
