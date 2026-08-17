"""Public geo lookup -- the requester's ISO country code (+ US subdivision), for
the cookie consent bar's geo-aware default (EU/UK/EEA + California opt-in, rest
opt-out).

Resolves the client IP against a local MaxMind GeoLite2-Country.mmdb via the
geoip2 package. No external call, no logging of the IP. Fail-soft = fail-closed
for privacy: if the DB is missing or the lookup fails, country is null and the
frontend treats that as opt-in (analytics stay off until the visitor accepts).

The `region` field is the ISO-3166-2 subdivision code (e.g. "CA" for California)
and is populated ONLY for US visitors, from two sources in order: (1) Cloudflare's
`cf-region-code` header when the "Add visitor location headers" managed transform
is enabled on the zone, then (2) the local MaxMind GeoLite2-City DB's subdivision.
The City DB path needs no Cloudflare config, so California is singled out server-
side by default; the header path is a no-config shortcut when the transform is on.
When neither yields a subdivision the field is null and the US visitor falls back
to opt-out. This is what makes first-time California visitors opt-in for CIPA --
see consent.js.

This endpoint is intentionally registered WITHOUT the machine X-Api-Key
dependency so the consent bar (which loads standalone, before auth) can call it
anonymously. It returns nothing sensitive -- a country code and, for the US, a
state code.
"""

import logging
import os

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["geo"])

_GEOIP_DB_PATH = os.environ.get("GEOIP_DB_PATH", "/geoip/GeoLite2-Country.mmdb")
_GEOIP_CITY_DB_PATH = os.environ.get("GEOIP_CITY_DB_PATH", "/geoip/GeoLite2-City.mmdb")

# Lazily-opened, process-wide readers. None = not yet tried; False = tried and
# unavailable (missing file / import error) so we don't retry on every request.
_reader = None       # GeoLite2-Country (the country lookup)
_city_reader = None  # GeoLite2-City (the US subdivision lookup; optional)


class GeoOut(BaseModel):
    country: str | None  # ISO-3166-1 alpha-2, or null when undetermined
    region: str | None = None  # ISO-3166-2 subdivision (US only, e.g. "CA"), or null


def _get_reader():
    global _reader
    if _reader is not None:
        return _reader or None
    try:
        import geoip2.database  # noqa: WPS433 (optional dependency)

        _reader = geoip2.database.Reader(_GEOIP_DB_PATH)
        logger.info("GeoIP country DB loaded from %s", _GEOIP_DB_PATH)
    except Exception as exc:  # missing file, missing package, corrupt db
        logger.warning("GeoIP unavailable (%s); /api/geo-country returns null", exc)
        _reader = False
    return _reader or None


def _get_city_reader():
    global _city_reader
    if _city_reader is not None:
        return _city_reader or None
    try:
        import geoip2.database  # noqa: WPS433 (optional dependency)

        _city_reader = geoip2.database.Reader(_GEOIP_CITY_DB_PATH)
        logger.info("GeoIP city DB loaded from %s", _GEOIP_CITY_DB_PATH)
    except Exception as exc:  # missing file (city DB is optional), corrupt db
        logger.info("GeoIP city DB unavailable (%s); US region falls back to header/null", exc)
        _city_reader = False
    return _city_reader or None


def _client_ip(request: Request) -> str | None:
    # Behind Cloudflare the real client IP is CF-Connecting-IP; nginx also sets
    # X-Forwarded-For (first hop = real client). Prefer CF, then XFF, then real-ip.
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else None


def _us_region(request: Request, ip: str | None, country: str | None) -> str | None:
    """US subdivision code (e.g. "CA") so California can get an opt-in default.
    Only meaningful for the US; null for non-US. Sources, in order: Cloudflare's
    cf-region-code header (when the managed transform is on), then the local
    GeoLite2-City subdivision (no Cloudflare config needed)."""
    if country != "US":
        return None
    code = request.headers.get("cf-region-code")
    if code:
        return code.strip().upper()
    reader = _get_city_reader()
    if reader is not None and ip:
        try:
            sub = reader.city(ip).subdivisions.most_specific.iso_code
            if sub:
                return sub.strip().upper()
        except Exception:
            # Private/local/unknown IP, or address-not-found in the city DB.
            logger.debug("geo: swallowed in _us_region", exc_info=True)
    return None


@router.get("/geo-country", response_model=GeoOut)
def geo_country(request: Request) -> GeoOut:
    reader = _get_reader()
    if reader is None:
        return GeoOut(country=None)
    ip = _client_ip(request)
    if not ip:
        return GeoOut(country=None)
    try:
        resp = reader.country(ip)
        country = resp.country.iso_code
        return GeoOut(country=country, region=_us_region(request, ip, country))
    except Exception:
        # Private/local/unknown IP, or address-not-found in the DB.
        logger.debug("geo: swallowed in geo_country", exc_info=True)
        return GeoOut(country=None)
