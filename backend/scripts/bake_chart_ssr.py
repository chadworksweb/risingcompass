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

from app.routers import page_ssr

# The baked pages are public, so force the canonical production origin
# regardless of the local SITE_URL env.
page_ssr._SITE = "https://risingcompass.net"

API_BASE = "https://risingcompass.net"
# Public read key (same one the static frontend ships in js/api.js).
API_KEY = "6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b"

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
