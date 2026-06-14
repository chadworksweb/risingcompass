"""Scrape Shield public endpoint: browser session-token grant (Layer 2).

The browser calls GET /api/session/grant on load. If the request is same-origin
(Origin/Referer is risingcompass.net), we mint a short-TTL, IP-bound HMAC token
and set it as an HttpOnly cookie. Subsequent same-origin /api/* fetches send the
cookie automatically (fetch default credentials), so the shield's token layer
passes for real browsers while a copied-key scraper -- which has no cookie and
no same-origin context -- does not.

No X-Api-Key dependency: the grant is the thing that bootstraps the browser, and
it is origin-gated + rate-limited instead. The endpoint is deliberately cheap
and stateless (no DB write).
"""

import logging

from fastapi import APIRouter, Request, Response

from app.config import settings
from app.services import scrape_shield as shield

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/session", tags=["session"])


@router.get("/grant")
async def grant_session_token(request: Request, response: Response):
    """Mint + set the browser session-token cookie for same-origin callers."""
    if not shield.origin_ok(request):
        # Not a same-origin browser context. Don't mint -- but don't hard-fail
        # either; return ok=False so the frontend degrades gracefully and the
        # shield's token layer (when enforced) is what actually blocks.
        return {"ok": False}

    ip = shield.client_ip(request)
    token = shield.mint_token(ip)
    secure = settings.site_url.startswith("https://")
    response.set_cookie(
        key=shield.COOKIE_NAME,
        value=token,
        max_age=shield.TOKEN_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return {"ok": True, "ttl": shield.TOKEN_TTL_SECONDS}
