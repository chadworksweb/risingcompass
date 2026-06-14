"""Scrape Shield -- the app-layer half of the Genius-style anti-scrape stack.

The paid machine API lives on api.risingcompass.net (X-Api-Key, billed). The
public site reuses the SAME backend with a single hardcoded "public" key baked
into the browser bundle, so that key is trivially copyable and, on its own,
gates nothing against a determined scraper. This module raises the cost of the
free path so bulk consumers go pay for the API:

  - client_ip()      -- the REAL client IP, behind Cloudflare + nginx.
  - origin_ok()      -- same-origin Origin/Referer check (browser path).
  - bot_score()      -- cheap UA/header heuristics -> (score, reasons).
  - mint_token()/verify_token() -- short-TTL, IP-bound HMAC session token that
                        replaces the static key as the browser's credential.
  - per-IP read rate limiting (sliding window, in-process).

Cloudflare (Layer 0, configured in the dashboard) does the heavy lifting:
verified-bot allowlisting, managed challenges, datacenter-ASN blocking, edge
rate limiting. This module is the backstop and the watermark/observe surface,
and is the only place that knows the browser token format.

Tunables live in system_flags (admin-toggleable, no redeploy); enforcement
ships OFF (observe-only) so we can watch the bot feed before turning teeth on.
All state is in-process (one backend container), so counters reset on restart
-- acceptable for rate limiting.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from collections import deque
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)


# --- real client IP --------------------------------------------------------
# Order of trust: Cloudflare's CF-Connecting-IP (set at the edge, not forgeable
# once traffic is proxied) -> X-Forwarded-For first hop (nginx) -> socket peer.
# uvicorn runs with --proxy-headers --forwarded-allow-ips '*', so
# request.client.host already reflects XFF; CF-Connecting-IP is preferred
# because once we're behind Cloudflare the XFF chain includes CF's own hops.
def client_ip(request: Request) -> str:
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


# --- same-origin check -----------------------------------------------------
# Hosts allowed to mint a browser session token. Origin/Referer are spoofable
# by a non-browser client, so this is NOT a hard wall on its own -- it stops
# the trivial "copy the key, curl the JSON" attack and, combined with the
# IP-bound short-TTL token + rate limiter + Cloudflare challenge, makes the
# free path expensive to automate.
_ALLOWED_ORIGIN_HOSTS = {
    "risingcompass.net",
    "www.risingcompass.net",
    "localhost",
    "127.0.0.1",
}


def _host_of(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return (urlsplit(value).hostname or "").lower() or None
    except ValueError:
        return None


def origin_ok(request: Request) -> bool:
    """True if the request looks same-origin (browser navigation/fetch)."""
    host = _host_of(request.headers.get("origin")) or _host_of(
        request.headers.get("referer")
    )
    if host is None:
        return False
    return host in _ALLOWED_ORIGIN_HOSTS


# --- bot scoring -----------------------------------------------------------
# Substrings that mark a non-browser client. Real listeners never send these.
_UA_DENY = (
    "python-requests", "python-urllib", "urllib", "httpx", "aiohttp",
    "curl/", "wget/", "go-http-client", "okhttp", "java/", "libwww",
    "scrapy", "node-fetch", "axios", "got (", "httpclient", "puppeteer",
    "playwright", "headlesschrome", "phantomjs", "selenium", "colly",
    "apache-httpclient", "ruby", "perl", "winhttp", "guzzlehttp",
)
# Verified-good crawlers we never want the app layer to throttle (Cloudflare
# verifies these properly at the edge; in-app we match by UA substring purely
# so a misconfigured rule can't accidentally deindex the site). SEO + LLM
# citability depend on these getting through.
_UA_ALLOW = (
    "googlebot", "bingbot", "duckduckbot", "applebot", "yandexbot",
    "baiduspider", "slurp", "gptbot", "oai-searchbot", "chatgpt-user",
    "perplexitybot", "claudebot", "anthropic-ai", "google-extended",
    "facebookexternalhit", "twitterbot", "linkedinbot", "slackbot",
    "discordbot", "telegrambot", "whatsapp", "bingpreview", "embedly",
)


def is_allowed_bot(user_agent: str) -> bool:
    ua = (user_agent or "").lower()
    return any(tok in ua for tok in _UA_ALLOW)


def bot_score(request: Request) -> tuple[int, list[str]]:
    """Cheap heuristic score for a read request. Higher = more bot-like.

    Returns (score, reasons). Verified crawlers short-circuit to 0 so SEO and
    LLM citation are never collateral. Tuning is deliberately conservative --
    real value comes from Cloudflare; this is the observe/backstop layer.
    """
    ua = (request.headers.get("user-agent") or "")
    ua_l = ua.lower()

    if is_allowed_bot(ua_l):
        return 0, []

    score = 0
    reasons: list[str] = []

    if not ua_l:
        score += 50
        reasons.append("ua_empty")
    else:
        for tok in _UA_DENY:
            if tok in ua_l:
                score += 60
                reasons.append(f"ua_deny:{tok}")
                break

    # Real browsers always send Accept-Language and an Accept that wants HTML
    # or JSON. Scripted clients usually omit one or both.
    if not request.headers.get("accept-language"):
        score += 20
        reasons.append("no_accept_language")
    accept = (request.headers.get("accept") or "").lower()
    if not accept or accept == "*/*":
        score += 15
        reasons.append("accept_wildcard")

    return score, reasons


# --- browser session token (Layer 2) ---------------------------------------
# A short-TTL, IP-bound HMAC token minted ONLY to same-origin requests. It
# replaces the static public key as the browser's credential: a scraper can't
# lift one stable value and reuse it for bulk -- it must run the grant flow per
# IP and rotate within the TTL, which is exactly what the rate limiter and
# Cloudflare challenge catch. Format: "{exp}.{hexsig}".
TOKEN_TTL_SECONDS = 1800  # 30 min; the frontend re-grants on load + on 401
COOKIE_NAME = "rc_t"


def _signing_secret() -> bytes:
    return (settings.scrape_shield_secret or settings.rc_admin_key or "rc-dev-shield").encode()


def _sign(payload: str) -> str:
    return hmac.new(_signing_secret(), payload.encode(), hashlib.sha256).hexdigest()[:40]


def mint_token(ip: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
    exp = int(time.time()) + ttl
    payload = f"{exp}:{ip}"
    return f"{exp}.{_sign(payload)}"


def verify_token(token: str | None, ip: str) -> bool:
    """True if `token` is a valid, unexpired, IP-bound session token."""
    if not token or "." not in token:
        return False
    exp_str, sig = token.split(".", 1)
    try:
        exp = int(exp_str)
    except ValueError:
        return False
    if exp < time.time():
        return False
    expected = _sign(f"{exp}:{ip}")
    return hmac.compare_digest(sig, expected)


# --- per-IP sliding-window read rate limiter (Layer 1) ---------------------
# In-process; one backend container so a single dict is authoritative. Window
# is a deque of request timestamps per IP, trimmed on each check. Two windows:
# a short burst window (per-minute) and a long enumeration window (per-day).
_WINDOWS: dict[str, deque] = {}
_WINDOWS_LOCK = threading.Lock()
_DAY = 86400
_MINUTE = 60
# Periodic GC so abandoned IPs don't leak memory.
_LAST_GC = [0.0]
_GC_INTERVAL = 600


def _gc(now: float) -> None:
    if now - _LAST_GC[0] < _GC_INTERVAL:
        return
    _LAST_GC[0] = now
    cutoff = now - _DAY
    dead = [ip for ip, dq in _WINDOWS.items() if not dq or dq[-1] < cutoff]
    for ip in dead:
        _WINDOWS.pop(ip, None)


def check_read_rate(ip: str, per_minute: int, per_day: int) -> tuple[bool, str | None]:
    """Record a read from `ip` and return (allowed, window_tripped).

    window_tripped is "minute" or "day" when blocked, else None. A blocked
    request is still recorded so a scraper hammering at the cap stays blocked.
    """
    now = time.time()
    with _WINDOWS_LOCK:
        _gc(now)
        dq = _WINDOWS.get(ip)
        if dq is None:
            dq = deque()
            _WINDOWS[ip] = dq
        # Trim to the day window.
        day_cutoff = now - _DAY
        while dq and dq[0] < day_cutoff:
            dq.popleft()
        minute_cutoff = now - _MINUTE
        minute_count = sum(1 for t in dq if t >= minute_cutoff)
        day_count = len(dq)
        dq.append(now)
        if minute_count >= per_minute:
            return False, "minute"
        if day_count >= per_day:
            return False, "day"
    return True, None


# --- cached shield config --------------------------------------------------
# Reading six flags from system_flags on every read request would be a DB hit
# per request, so cache the whole config in-process for a short TTL (same
# pattern as analyzer._LC_LIMIT_CACHE). An admin toggle propagates within
# _CONFIG_TTL seconds.
_CONFIG_CACHE: dict = {"data": None, "ts": 0.0}
_CONFIG_TTL = 30.0


def _shield_config() -> dict:
    now = time.monotonic()
    if _CONFIG_CACHE["data"] is None or now - _CONFIG_CACHE["ts"] > _CONFIG_TTL:
        from app.database import SessionLocal
        from app.services import feature_flags as ff
        try:
            db = SessionLocal()
            try:
                data = {
                    "ratelimit_enabled": ff.is_shield_ratelimit_enabled(db),
                    "botscore_enabled": ff.is_shield_botscore_enabled(db),
                    "token_enabled": ff.is_shield_token_enabled(db),
                    "per_minute": ff.shield_read_per_minute(db),
                    "per_day": ff.shield_read_per_day(db),
                    "botscore_threshold": ff.shield_botscore_threshold(db),
                }
            finally:
                db.close()
            _CONFIG_CACHE.update(data=data, ts=now)
        except Exception:
            logger.exception("Failed to read scrape_shield config; failing OPEN")
            # Fail OPEN: never let a flag-read error wall off the public site.
            return {
                "ratelimit_enabled": False, "botscore_enabled": False,
                "token_enabled": False, "per_minute": 120, "per_day": 3000,
                "botscore_threshold": 60,
            }
    return _CONFIG_CACHE["data"]


def invalidate_config_cache() -> None:
    """Force the next request to re-read flags (called by the admin toggle)."""
    _CONFIG_CACHE["ts"] = 0.0


# --- event logging (throttled) ---------------------------------------------
# Log shield decisions to lc_events (no new table). Throttle per-IP so a
# scraper hammering at the cap doesn't write a row per request.
_LOG_THROTTLE: dict[str, float] = {}
_LOG_THROTTLE_SECS = 60.0
_LOG_LOCK = threading.Lock()


def _should_log(ip: str) -> bool:
    now = time.monotonic()
    with _LOG_LOCK:
        if now - _LOG_THROTTLE.get(ip, 0.0) < _LOG_THROTTLE_SECS:
            return False
        _LOG_THROTTLE[ip] = now
        if len(_LOG_THROTTLE) > 5000:  # bound memory
            cutoff = now - _LOG_THROTTLE_SECS
            for k in [k for k, t in _LOG_THROTTLE.items() if t < cutoff]:
                _LOG_THROTTLE.pop(k, None)
    return True


def _log_shield_event(request: Request, event_type: str, payload: dict) -> None:
    try:
        from app.services.lc_events import write_event
        ip = client_ip(request)
        if not _should_log(ip):
            return
        write_event(
            event_type,
            ip,
            (request.headers.get("user-agent") or "")[:500],
            (request.headers.get("referer") or "")[:500],
            payload=payload,
        )
    except Exception:
        logger.exception("shield event log failed")


# --- the guard dependency --------------------------------------------------
def _funnel_403(reason: str) -> HTTPException:
    """Block response that sells the paid API instead of just erroring."""
    return HTTPException(
        status_code=403,
        detail={
            "error": "automated_access_blocked",
            "reason": reason,
            "message": (
                "This looks like automated access. Bulk and programmatic use of "
                "Rising Compass runs through the licensed API at "
                "https://api.risingcompass.net -- see https://risingcompass.net/api "
                "for plans and a key."
            ),
        },
    )


async def shield_read_guard(request: Request) -> None:
    """Dependency for public READ endpoints. Runs AFTER verify_api_key so it can
    read request.state.client_behavior. Service-tier (paid / first-party) keys
    bypass entirely -- they ARE the sanctioned path. Each layer blocks only when
    its enable flag is on; otherwise it scores + logs (observe mode).
    """
    behavior = getattr(request.state, "client_behavior", None)
    if behavior == "service":
        return

    ua = request.headers.get("user-agent") or ""
    # Verified crawlers (Googlebot etc.) bypass throttling so SEO + LLM citation
    # are never collateral. Cloudflare verifies these properly at the edge.
    if is_allowed_bot(ua):
        return

    cfg = _shield_config()
    ip = client_ip(request)

    # Layer 3: bot score.
    score, reasons = bot_score(request)
    if score >= cfg["botscore_threshold"]:
        if cfg["botscore_enabled"]:
            _log_shield_event(request, "shield_block_botscore",
                              {"score": score, "reasons": reasons, "path": request.url.path})
            raise _funnel_403("bot_signature")
        _log_shield_event(request, "shield_observe_botscore",
                          {"score": score, "reasons": reasons, "path": request.url.path})

    # Layer 1: per-IP read rate limit. Always meter (cheap, in-process); only
    # block when enabled.
    allowed, window = check_read_rate(ip, cfg["per_minute"], cfg["per_day"])
    if not allowed:
        if cfg["ratelimit_enabled"]:
            _log_shield_event(request, "shield_block_ratelimit",
                              {"window": window, "path": request.url.path})
            raise _funnel_403(f"rate_limited_{window}")
        _log_shield_event(request, "shield_observe_ratelimit",
                          {"window": window, "path": request.url.path})

    # Layer 2: origin-bound session token. Valid token cookie OR a same-origin
    # request passes; everything else is a copied-key scraper.
    token = request.cookies.get(COOKIE_NAME)
    if not (verify_token(token, ip) or origin_ok(request)):
        if cfg["token_enabled"]:
            _log_shield_event(request, "shield_block_token", {"path": request.url.path})
            raise _funnel_403("no_session_token")
        _log_shield_event(request, "shield_observe_token", {"path": request.url.path})
