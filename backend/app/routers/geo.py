"""Public geo lookup -- the requester's ISO country code (+ US subdivision), for
the cookie consent bar's geo-aware default (EU/UK/EEA + California opt-in, rest
opt-out).

Resolves the client IP against a local MaxMind GeoLite2-Country.mmdb via the
geoip2 package. No external call, no logging of the IP. Fail-soft = fail-closed
for privacy: if the DB is missing or the lookup fails, country is null and the
frontend treats that as opt-in (analytics stay off until the visitor accepts).

The `region` field is the ISO-3166-2 subdivision code (e.g. "CA" for California)
and is populated ONLY for US visitors, from Cloudflare's `cf-region-code` header.
That header requires the Cloudflare "Add visitor location headers" managed
transform to be enabled on the zone; when it is off the field is null and US
visitors fall back to the opt-out default (California is not singled out). This
is what makes first-time California visitors opt-in for CIPA -- see consent.js.

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

# Lazily-opened, process-wide reader. None = not yet tried; False = tried and
# unavailable (missing file / import error) so we don't retry on every request.
_reader = None


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


def _client_ip(request: Request) -> str | None:
    # nginx sets X-Forwarded-For; take the first hop (the real client).
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else None


def _us_region(request: Request, country: str | None) -> str | None:
    """US subdivision code (e.g. "CA") from Cloudflare's visitor-location
    headers, so California can get an opt-in default. Only meaningful for the US
    (California opt-in); null for non-US or when the managed transform is off."""
    if country != "US":
        return None
    code = request.headers.get("cf-region-code")
    return code.strip().upper() if code else None


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
        return GeoOut(country=country, region=_us_region(request, country))
    except Exception:
        # Private/local/unknown IP, or address-not-found in the DB.
        return GeoOut(country=None)
