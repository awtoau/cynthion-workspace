#!/usr/bin/env python3
"""Fetch the Winbond HyperRAM datasheets into sources/.

winbond.com will not serve these. Its documentation pages render links via JS,
its search strips the keyword parameter, and the datasheet download endpoint
sits at `level=4`, which redirects to a login-walled support form. The PDFs come
from third-party mirrors instead; see sources/README.md for the full write-up.

The pieces that made it work, and why each subcommand exists:

  links  - winbond.com anchors DO come back once the page's JS has run, so drive
           the shared awto-playwrong headed Chrome and read the DOM. This is how
           the real `downloadV2022.jsp?xmlPath=...&level=N` endpoint was found.
  search - Bing wraps every result in `bing.com/ck/a?...&u=a1<base64url>`; decode
           that payload to recover the true target URL. Plain-curl DuckDuckGo
           returns HTTP 202 with an "anomaly" page and is useless here.
  get    - plain curl with a browser UA. Verifies the result is really a PDF:
           Mouser serves a ~13 KB HTML bot page under a .pdf name.

Usage:
    scripts/fetch_winbond_hyperram.py all                     # fetch both datasheets
    scripts/fetch_winbond_hyperram.py links <url> [substr]    # anchors after JS
    scripts/fetch_winbond_hyperram.py search <query>          # Bing, URLs decoded
    scripts/fetch_winbond_hyperram.py get <url> <outfile>     # download + verify

Logs to tmp/logs/fetch_winbond_hyperram.log.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLAYWRONG = Path("/mnt/2tb/git/awto-playwrong")
BASE = "http://127.0.0.1:8731"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Mirrors that actually serve these. Note xonstorage's `.z8.web` host: the
# `.blob` variant of the same name 404s.
WANTED = [
    (
        "https://xonstorage.z8.web.core.windows.net/pdf/"
        "winbond_w956d8mbya6i_apr22_xonlink.pdf",
        "sources/Winbond-W956A8MBYA-64Mbit-HyperRAM.pdf",
        "W956D8MBYA / W956A8MBYA, 64 Mbit -- the part on the Cynthion r1.4 board",
    ),
    (
        "https://media.digikey.com/pdf/Data%20Sheets/Winbond%20PDFs/"
        "W957x8MFYA_Rev_A01-004_8-4-22.pdf",
        "sources/Winbond-W956D8MBY-128Mbit-HyperRAM.pdf",
        "W957D8MFYA / W957A8MFYA, 128 Mbit DDP -- the nearest true 128 Mbit part",
    ),
]

LOG_DIR = REPO / "tmp" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "fetch_winbond_hyperram.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("fetch-winbond")

sys.path.insert(0, str(PLAYWRONG))


def engine_op(op: str, **body):
    """Drive the shared awto-playwrong engine, starting it if it is down."""
    from engine import connect  # noqa: PLC0415  (needs sys.path set above)

    connect.ensure()
    return connect.op(op, **body)


def links(url: str, substr: str = "") -> list[tuple[str, str]]:
    """Anchors as rendered, i.e. after the page's JS has populated them."""
    engine_op("goto", url=url)
    expr = (
        'JSON.stringify(Array.from(document.querySelectorAll("a"))'
        '.map(a=>[a.textContent.trim().slice(0,90), a.href]).filter(x=>x[1]))'
    )
    raw = engine_op("js", expr=expr).get("result")
    out = json.loads(raw) if isinstance(raw, str) else (raw or [])
    if substr:
        s = substr.lower()
        out = [x for x in out if s in (x[1] or "").lower() or s in (x[0] or "").lower()]
    return out


def search(query: str) -> list[str]:
    """Bing results with the ck/a redirect wrapper decoded back to real URLs."""
    engine_op("goto", url="https://www.bing.com/search?q=" + urllib.parse.quote(query))
    expr = (
        'JSON.stringify(Array.from(document.querySelectorAll("a"))'
        '.map(a=>a.href).filter(h=>/bing\\.com\\/ck\\/a/.test(h)))'
    )
    raw = engine_op("js", expr=expr).get("result")
    wrapped = json.loads(raw) if isinstance(raw, str) else (raw or [])
    out, seen = [], set()
    for href in wrapped:
        m = re.search(r"[?&]u=a1([A-Za-z0-9_\-]+)", href)
        if not m:
            continue
        try:
            # padded to a multiple of 4; urlsafe_b64decode ignores the excess
            real = base64.urlsafe_b64decode(m.group(1) + "==").decode()
        except Exception:
            continue
        if real.startswith("http") and real not in seen:
            seen.add(real)
            out.append(real)
    return out


def get(url: str, out: str) -> bool:
    """Download and verify. A .pdf URL returning HTML is a bot wall, not a hit."""
    outp = out if Path(out).is_absolute() else REPO / out
    outp = Path(outp)
    outp.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["curl", "-sL", "-m", "180", "-A", UA, "--compressed", url, "-o", str(outp)],
        check=False,
    )
    if not outp.exists():
        log.error("no file written for %s", url)
        return False
    kind = subprocess.run(
        ["file", "-b", str(outp)], capture_output=True, text=True
    ).stdout.strip()
    log.info("%s -> %s (%d bytes) %s", url, outp, outp.stat().st_size, kind)
    if "PDF document" not in kind:
        log.error("NOT A PDF -- %s is %s (bot wall?)", outp, kind)
        return False
    return True


def fetch_all() -> int:
    bad = 0
    for url, dest, what in WANTED:
        log.info("fetching %s", what)
        if not get(url, dest):
            bad += 1
    log.info("done: %d/%d ok", len(WANTED) - bad, len(WANTED))
    return bad


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "all":
        return 1 if fetch_all() else 0
    if cmd == "links":
        for text, href in links(args[0], args[1] if len(args) > 1 else ""):
            print(f"{href}\t{text}")
    elif cmd == "search":
        for u in search(" ".join(args)):
            print(u)
    elif cmd == "get":
        return 0 if get(args[0], args[1]) else 1
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
