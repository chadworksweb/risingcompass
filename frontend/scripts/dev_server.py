#!/usr/bin/env python3
"""Local dev server with the same clean-URL rewrites nginx does on prod.

Plain `python -m http.server 3005` serves the static frontend fine, but
404s on `/songs/<slug>`, `/artists/<slug>`, etc -- prod nginx silently
rewrites those to the canonical HTML file. This wrapper mirrors that
behavior so links you copy out of prod work locally too.

Usage:
  cd frontend
  python scripts/dev_server.py            # default port 3005
  python scripts/dev_server.py --port 4005

Rewrite rules (path -> served file, only when the path does not match a
real file or directory):
  /songs/<slug>        -> /songs/song.html
  /artists/<slug>      -> /artists/artist.html
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent  # the frontend/ directory


# (regex, served-file) pairs. First match wins. Slugs are any
# non-slash chars; the trailing slash is optional.
REWRITES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^/songs/[^/]+/?$"), "/songs/song.html"),
    (re.compile(r"^/artists/[^/]+/?$"), "/artists/artist.html"),
    (re.compile(r"^/motion-desk/deliberation-chamber/\d+/?$"),
     "/motion-desk/deliberation-chamber/index.html"),
]


def _resolve_against_disk(path: str) -> Path:
    """Map a URL path to a candidate filesystem path. Used to decide whether
    a request should hit a real file or fall through to a rewrite."""
    # Strip the leading slash so Path() treats it as relative to ROOT.
    rel = path.lstrip("/")
    return (ROOT / rel).resolve()


class RewritingHandler(SimpleHTTPRequestHandler):
    # Serve from the frontend/ directory regardless of where the script
    # was launched from.
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):  # noqa: N802 -- http.server contract
        rewritten = self._maybe_rewrite()
        if rewritten is not None:
            self.path = rewritten
        return super().do_GET()

    def _maybe_rewrite(self) -> str | None:
        url = urlsplit(self.path)
        path = url.path
        # If the file or directory actually exists, do nothing -- let
        # SimpleHTTPRequestHandler serve it normally (including dir indexes).
        on_disk = _resolve_against_disk(path)
        if on_disk.exists():
            return None
        for pattern, target in REWRITES:
            if pattern.match(path):
                # Preserve the original querystring / fragment by handing
                # SimpleHTTPRequestHandler back a path with the rewritten
                # file portion but the original query.
                query = f"?{url.query}" if url.query else ""
                return f"{target}{query}"
        return None

    def log_message(self, fmt, *args):  # quieter access log
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3005)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    os.chdir(ROOT)
    server = ThreadingHTTPServer((args.host, args.port), RewritingHandler)
    print(f"dev server: http://{args.host}:{args.port}/  (root: {ROOT})")
    print("Rewrites: /songs/<slug> -> /songs/song.html, /artists/<slug> -> /artists/artist.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
