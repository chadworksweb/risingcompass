"""Bake server-rendered bodies into the STATIC pages nginx serves directly.

Sibling to `bake_chart_ssr.py`, same reasoning and same lane. A handful of
public pages are client-rendered and do NOT route through page_ssr, because
nginx serves them off disk: their raw HTML is an empty container, so a crawler
that does not execute JS sees a shell. Rather than add an nginx location per
page -- config that lives on the server and not in git, and that a proxy
rebuild silently drops -- we pre-bake the same markup into the file.

Baked here, and why each is a fit for baking rather than a route:

    /artists/   the A-Z index. ~1,500 links, 47 characters of raw body text
                before this. The roster changes slowly.
    /shop/      three products, currently "Loading the shop..." to a crawler.

Deliberately NOT baked:

    /library/           its rows are entitlement-gated (the free/anon payload
                        caps at 20 and withholds prose), and the table is
                        column-config driven. Mirroring that server-side forks
                        a paywall decision into a second implementation, and
                        the whole prize is 20 links to songs the sitemap
                        already lists. Not worth the risk of leaking gated
                        rows into a static file.
    /shop/product.html  one file serving many products via `?p=slug`. A
                        query-string URL cannot be baked per product; the
                        detail pages need a path-based URL first, which is a
                        routing change, not a rendering one.
    the daily pages     homepage, the six daily/weekly chart pages, ether art
                        chart, charger activity, calibration log. Their data
                        turns over daily, so a deploy-time bake is stale
                        within a day. Those want real nginx routes.

Idempotent: every renderer it calls REPLACES the container's contents rather
than filling an empty one, so re-running over an already-baked file re-bakes
rather than nesting. Run it after each deploy (deploy.sh does), then commit the
changed pages.

    cd backend && .venv/Scripts/python.exe scripts/bake_static_ssr.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app.config import settings
from app.routers import page_ssr

# The baked pages are public, so the canonical origin is forced regardless of
# the local SITE_URL env. Same reasoning as bake_chart_ssr.
page_ssr._SITE = "https://risingcompass.net"

# Fetch from the machine-API host, NOT the public root: the root sits behind
# Cloudflare's bot challenge, which 403s a non-browser client like this one.
API_BASE = "https://api.risingcompass.net"
API_KEY = settings.rc_service_key or "6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b"

# page -> (template path, api path, renderer, key that must be non-empty)
PAGES = {
    "artists-index": ("artists/index.html", "/api/artists",
                      page_ssr._ssr_artists_index_body, "artists"),
    "shop": ("shop/index.html", "/api/shop/products",
             page_ssr._ssr_shop_body, "products"),
}


def main() -> int:
    failures = 0
    for name, (tpl_path, api_path, render, key) in PAGES.items():
        try:
            resp = httpx.get(API_BASE + api_path,
                             headers={"X-Api-Key": API_KEY}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            # A fetch failure must leave the existing page alone. Baking an
            # empty list over a good page would blank a live index.
            print(f"{name}: fetch failed ({exc}); leaving page as-is")
            failures += 1
            continue

        rows = data.get(key) or []
        if not rows:
            print(f"{name}: API returned no {key}, skipping")
            continue

        fp = page_ssr._FRONTEND_DIR / tpl_path
        html = fp.read_text(encoding="utf-8")
        baked = render(html, data)
        if baked == html:
            print(f"{name}: unchanged")
            continue
        fp.write_text(baked, encoding="utf-8", newline="\n")
        print(f"{name}: baked {len(rows)} {key} -> {tpl_path}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
