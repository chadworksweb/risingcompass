"""Server-side render of a charge card to PNG bytes via Playwright.

The card is authored ONCE in JS (frontend/lyrical-charger/rc-charge-card-generator.js)
and rendered for the browser. Rather than re-implement it in Pillow, the cron
drives a headless Chromium to the render surface (frontend/cards/index.html),
which calls the same RCChargeCard.render / renderReading and exposes
window.__cardReady + window.__cardPng(). We screenshot the canvas through that
contract, so per-song and daily-aggregate social cards are pixel-identical to
the ones users get from the site.

Requires Chromium installed for Playwright (`playwright install chromium`); the
prod image must run that at build time. Fail-soft is the caller's concern --
this raises on any render failure so the broadcaster can ledger the error.
"""

import base64
import json
import logging

from playwright.async_api import async_playwright

from app.config import settings

logger = logging.getLogger(__name__)


def _encode_data(data: dict) -> str:
    # URL-safe base64 so '+' / '/' in the payload survive the query string
    # (a plain '+' would be decoded back to a space by URLSearchParams). The
    # render page already maps '-'/'_' back before atob.
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


async def render_card(card_type: str, data: dict, *, base_url: str | None = None,
                      timeout_ms: int = 30000) -> bytes:
    """Render the 'song' or 'reading' card for `data` and return PNG bytes.

    Navigates {base}/cards/?type=<card_type>&data=<b64>, waits for the page's
    ready flag (which only flips after ensureFonts + the canvas draw resolve),
    then reads the 1080x1080 PNG data URL the page exposes.
    """
    if card_type not in ("song", "reading"):
        raise ValueError(f"unknown card_type {card_type!r}")
    base = (base_url or settings.card_render_base_url).rstrip("/")
    url = f"{base}/cards/?type={card_type}&data={_encode_data(data)}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = await browser.new_page(
                viewport={"width": 1140, "height": 1140},
                device_scale_factor=1,
            )
            await page.goto(url, wait_until="load", timeout=timeout_ms)
            # __cardReady flips true only after the async render() resolves; if
            # the page hit a render error it stays false and this times out.
            await page.wait_for_function("window.__cardReady === true", timeout=timeout_ms)
            data_url = await page.evaluate("window.__cardPng && window.__cardPng()")
        finally:
            await browser.close()

    if not data_url or "," not in data_url:
        raise RuntimeError(f"card render returned no PNG for {card_type}")
    return base64.b64decode(data_url.split(",", 1)[1])
