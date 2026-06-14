"""Bake schema.org JSON-LD + the server-rendered ranked list into the STATIC
all-time chart pages, for GEO/SEO.

The chart pages are otherwise client-rendered (an empty #chart-root in the raw
HTML), invisible to answer-engine crawlers. Rather than route the pages through
the shared nginx proxy for dynamic SSR, we pre-bake the same output (an ItemList
+ FAQPage in the head + the ranked list in the body) directly into the static
index.html files nginx already serves. Charts change monthly, so static-baked
is the right fit. alltime.js still hydrates the interactive view on load.

Idempotent (reuses page_ssr.inject_chart_ssr, which strips any prior bake), so
re-run it after each monthly refresh, then commit + deploy the changed pages.

Run locally against the live API:
    cd backend && .venv/Scripts/python.exe scripts/bake_chart_ssr.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app.config import settings
from app.routers import page_ssr

# The baked pages are public, so force the canonical production origin
# regardless of the local SITE_URL env (this is the canonical URL written into
# the baked HTML, NOT the host we fetch the data from).
page_ssr._SITE = "https://risingcompass.net"

# Fetch from the machine-API host, NOT the public root. The public root now
# sits behind Cloudflare's bot challenge + the app-layer Scrape Shield, which
# 403 a non-browser client like this baker. The api host is the sanctioned
# machine path: no Cloudflare challenge, and a service-tier key bypasses the
# Scrape Shield (shield_read_guard returns early for behavior == "service").
API_BASE = "https://api.risingcompass.net"
# Service key (first-party, bypasses the shield). Falls back to the public read
# key (the one the static frontend ships) when RC_SERVICE_KEY is unset -- the
# api host accepts it too, since the shield ships observe-only.
API_KEY = settings.rc_service_key or "6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b"

ENDPOINTS = {
    "streamed-all-time": "/api/charts/alltime/streams",
    "most-streamed-albums": "/api/charts/alltime/stream-albums",
    "best-selling-albums": "/api/charts/alltime/albums",
}


def main():
    cfgs = page_ssr._chart_configs()
    for kind, cfg in cfgs.items():
        resp = httpx.get(
            API_BASE + ENDPOINTS[kind],
            headers={"X-Api-Key": API_KEY},
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json().get("rows", [])
        if not rows:
            print(f"{kind}: no rows from API, skipping")
            continue
        fp = page_ssr._FRONTEND_DIR / cfg["template"]
        html = fp.read_text(encoding="utf-8")
        baked = page_ssr.inject_chart_ssr(html, kind, cfg, rows)
        fp.write_text(baked, encoding="utf-8", newline="\n")
        has_ld = '"@type": "ItemList"' in baked
        print(f"{kind}: baked {len(rows)} rows -> {fp.name} (ItemList: {has_ld})")


if __name__ == "__main__":
    main()
