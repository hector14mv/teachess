"""Download every monthly game archive of a chess.com player via the public PubAPI.

Usage: python3 fetch_games.py [username]   (default: TEACHESS_USER or config.json)
Writes data/raw/YYYY-MM.json (one file per month). Past months are cached;
the current month is always refetched. Requests are serial: chess.com rate-limits parallel calls.
"""

import gzip
import http.client
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
from datetime import date

from . import __version__, config, paths

# chess.com asks API clients to say who they are and where to reach them
UA = f"teachess/{__version__} (+https://github.com/hector14mv/teachess)"
RAW = paths.DATA / "raw"


_local = threading.local()  # one kept-alive connection per thread: a new TLS handshake costs more than a month


def request(method: str, url: str) -> tuple[http.client.HTTPResponse, bytes]:
    """One serial request over a kept-alive connection to chess.com. Follows redirects, retries a dropped
    connection and a 429 (rate limit) with backoff; other HTTP errors raise urllib.error.HTTPError, a dead
    network URLError."""
    headers = {"User-Agent": UA}
    if method == "GET":
        headers["Accept-Encoding"] = "gzip"  # chess.com asks for it; a month shrinks about 8x
    hops = 0
    for attempt in range(5):
        parts = urllib.parse.urlsplit(url)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        conn = getattr(_local, "conn", None)
        if conn is None or conn.host != parts.hostname:
            conn = _local.conn = http.client.HTTPSConnection(parts.hostname, timeout=30)
        try:
            conn.request(method, target, headers=headers)
            r = conn.getresponse()
            body = r.read()
        except (http.client.HTTPException, OSError) as e:
            conn.close()
            _local.conn = None
            if attempt == 4:
                raise urllib.error.URLError(e) from None
            continue
        if r.status in (301, 302, 303, 307, 308) and r.getheader("Location") and hops < 5:
            url, hops = urllib.parse.urljoin(url, r.getheader("Location")), hops + 1  # chess.com moves some URLs
            continue
        if r.status == 429:
            time.sleep(2 ** attempt)
            continue
        if r.status >= 400:
            raise urllib.error.HTTPError(url, r.status, r.reason, r.headers, None)
        if r.getheader("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return r, body
    raise RuntimeError(f"rate-limited too many times: {url}")


def get(url: str) -> dict:
    return json.loads(request("GET", url)[1])


def size(url: str) -> int:
    """Bytes of a monthly archive without downloading it (a HEAD request; chess.com sends Content-Length)."""
    return int(request("HEAD", url)[0].getheader("Content-Length") or 0)


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
