#!/usr/bin/env python3
"""Local dev server: clean-URL rewrites + an API reverse proxy.

Plain `python -m http.server 3005` serves the static frontend fine, but
404s on `/songs/<slug>`, `/artists/<slug>`, etc -- prod nginx silently
rewrites those to the canonical HTML file. It also can't reach the backend.

This wrapper mirrors prod's single-origin shape:
  * clean-URL rewrites (/songs/<slug> -> /songs/song.html, etc), and
  * a reverse proxy: any request to /api/... or /rc-admin-... is forwarded
    to the backend at http://127.0.0.1:8000.

Because the frontend now uses a RELATIVE API base ('' -> same origin), the
backend MUST be reached through this proxy -- `python -m http.server` will
404 every /api/ call. Always launch the frontend with this script.

Usage:
  cd frontend
  python scripts/dev_server.py            # default port 3005
  python scripts/dev_server.py --port 4005
  python scripts/dev_server.py --backend http://127.0.0.1:9000

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
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent  # the frontend/ directory

# Backend origin the proxy forwards to. Overridable with --backend.
BACKEND = "http://127.0.0.1:8000"

# Path prefixes proxied to the backend. Mirrors the nginx root-block
# `location /api/`, `location ~ ^/rc-admin-`, and `location /feeds/` rules on
# prod. The chart RSS feeds are backend-rendered, so they need the same
# forwarding the API does.
PROXY_PREFIXES = ("/api/", "/rc-admin-", "/feeds/")

# Request/response headers that must not be copied verbatim through a proxy.
_HOP_BY_HOP_REQ = {"host", "content-length", "connection", "accept-encoding", "keep-alive"}
_HOP_BY_HOP_RESP = {"transfer-encoding", "connection", "content-encoding", "content-length", "keep-alive"}


# (regex, served-file) pairs. First match wins. Slugs are any
# non-slash chars; the trailing slash is optional.
REWRITES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^/songs/[^/]+/?$"), "/songs/song.html"),
    (re.compile(r"^/artists/[^/]+/?$"), "/artists/artist.html"),
    (re.compile(r"^/motion-desk/deliberation-chamber/\d+/?$"),
     "/motion-desk/deliberation-chamber/index.html"),
]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stop urllib from following 3xx server-side -- the browser must see the
    redirect (e.g. /api/admin/dashboard -> /dashboard/db) so the address bar
    and same-origin cookie semantics match prod."""

    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _resolve_against_disk(path: str) -> Path:
    """Map a URL path to a candidate filesystem path. Used to decide whether
    a request should hit a real file or fall through to a rewrite."""
    # Strip the leading slash so Path() treats it as relative to ROOT.
    rel = path.lstrip("/")
    return (ROOT / rel).resolve()


def _should_proxy(path: str) -> bool:
    return any(path.startswith(p) for p in PROXY_PREFIXES)


# Detail pages are server-rendered by the backend (per-entity meta + JSON-LD),
# so proxy the dotless slug paths there instead of rewriting to the static
# file. The [^/.]+ excludes real assets (song.html, songs.js, songs.css),
# which keep being served statically. Mirrors the prod nginx
# `location ~ ^/(songs|artists)/[^/.]+$` rule.
SSR_PROXY_PATTERNS = [
    re.compile(r"^/songs/[^/.]+/?$"),
    re.compile(r"^/artists/[^/.]+/?$"),
    # Release detail: /artists/<artist>/<release> -> backend SSR (release.html
    # + injected meta). Two dotless segments, so it never catches asset files.
    re.compile(r"^/artists/[^/.]+/[^/.]+/?$"),
    # All-time chart pages: backend SSR bakes a schema.org ItemList + FAQ and
    # server-renders the ranked list into the body (the page is otherwise an
    # empty client-rendered mount point). Exact paths only.
    re.compile(r"^/charts/streamed-all-time/?$"),
    re.compile(r"^/charts/most-streamed-albums/?$"),
    re.compile(r"^/charts/best-selling-albums/?$"),
]


def _should_ssr_proxy(path: str) -> bool:
    return any(p.match(path) for p in SSR_PROXY_PATTERNS)


class RewritingHandler(SimpleHTTPRequestHandler):
    # Serve from the frontend/ directory regardless of where the script
    # was launched from.
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # Dev only: never let the browser cache, so phone/LAN reloads always get the
    # latest edited files without a hard refresh.
    def end_headers(self):  # noqa: N802 -- http.server contract
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    # --- Static (GET/HEAD) ------------------------------------------------
    def do_GET(self):  # noqa: N802 -- http.server contract
        path = urlsplit(self.path).path
        if _should_proxy(path) or _should_ssr_proxy(path):
            return self._proxy()
        rewritten = self._maybe_rewrite()
        if rewritten is not None:
            self.path = rewritten
        return super().do_GET()

    def do_HEAD(self):  # noqa: N802
        path = urlsplit(self.path).path
        if _should_proxy(path) or _should_ssr_proxy(path):
            return self._proxy()
        rewritten = self._maybe_rewrite()
        if rewritten is not None:
            self.path = rewritten
        return super().do_HEAD()

    # --- Proxied verbs ----------------------------------------------------
    def do_POST(self):  # noqa: N802
        return self._proxy_or_405()

    def do_PUT(self):  # noqa: N802
        return self._proxy_or_405()

    def do_PATCH(self):  # noqa: N802
        return self._proxy_or_405()

    def do_DELETE(self):  # noqa: N802
        return self._proxy_or_405()

    def _proxy_or_405(self):
        if _should_proxy(urlsplit(self.path).path):
            return self._proxy()
        self.send_error(405, "Method Not Allowed")

    def _proxy(self):
        """Forward the current request to the backend and relay the response,
        preserving status, Set-Cookie, and body. Redirects are passed through
        (not followed) so the browser handles them."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None

        fwd_headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in _HOP_BY_HOP_REQ
        }
        target = BACKEND + self.path
        req = urllib.request.Request(
            target, data=body, method=self.command, headers=fwd_headers,
        )

        try:
            resp = _opener.open(req, timeout=1800)
            status, resp_headers, resp_body = resp.status, resp.getheaders(), resp.read()
            resp.close()
        except urllib.error.HTTPError as e:
            # Non-2xx (incl. 3xx with redirects disabled, 4xx, 5xx). Relay it.
            status, resp_headers, resp_body = e.code, list(e.headers.items()), e.read()
        except Exception as e:  # noqa: BLE001 -- surface as 502 to the browser
            self.send_error(502, f"proxy error: {e}")
            return

        self.send_response(status)
        for k, v in resp_headers:
            if k.lower() in _HOP_BY_HOP_RESP:
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(resp_body)

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
    global BACKEND
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3005)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--backend", default=BACKEND,
                    help="backend origin proxied for /api and /rc-admin- (default %(default)s)")
    args = ap.parse_args()

    BACKEND = args.backend.rstrip("/")

    os.chdir(ROOT)
    server = ThreadingHTTPServer((args.host, args.port), RewritingHandler)
    print(f"dev server: http://{args.host}:{args.port}/  (root: {ROOT})")
    print(f"proxy: /api/* and /rc-admin-* -> {BACKEND}")
    print("rewrites: /songs/<slug>, /artists/<slug>, /motion-desk/deliberation-chamber/<id>")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
