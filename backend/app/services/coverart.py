"""Cover Art Archive (CAA) client + lookup cache.

CAA hosts community-supplied cover art keyed by MusicBrainz IDs. We query by
the release-GROUP MBID (the canonical album cover), which is exactly what the
`releases.musicbrainz_id` column already stores.

Legal posture: CAA grants ACCESS, not a license -- the images stay copyrighted
by the labels/artists. We HOTLINK the CAA shortcut URLs (we stay a referrer,
never a host) and display them for release identification. has_art=False is a
recorded "checked, none" so we don't broken-img and don't re-query.

Free API, CDN-fronted. No key required; we still send the project User-Agent and
throttle politely. Display URLs are constructed from the MBID -- the front-NNN
shortcut endpoints are stable and 307-redirect to Internet Archive storage.
"""

import asyncio
import logging
import time
from datetime import datetime

import httpx

from app.database import SessionLocal
from app.models import MbCoverArt

logger = logging.getLogger(__name__)

BASE_URL = "https://coverartarchive.org"
USER_AGENT = "RisingCompass/1.0 (https://risingcompass.net)"

# Thumbnail for the artist-page release list; larger front for the release page
# hero. Both are stable CAA shortcut URLs that 307 to IA storage.
THUMB_SIZE = 250
COVER_SIZE = 500

# Polite throttle. CAA is CDN-fronted and not hard rate-limited like the main
# MusicBrainz API, but the backfill is the only volume and 1/sec keeps us a
# good citizen on Internet-Archive-backed infra.
_last_request_time: float = 0


def coverart_urls(musicbrainz_id: str) -> dict:
    """Construct the (thumb, cover) CAA shortcut URLs for a release-group MBID.

    Stable regardless of where IA stores the bytes; callers should only use
    these when the MBID is known to have art (has_art=True).
    """
    base = f"{BASE_URL}/release-group/{musicbrainz_id}"
    return {
        "thumb_url": f"{base}/front-{THUMB_SIZE}",
        "cover_url": f"{base}/front-{COVER_SIZE}",
    }


async def _rate_limit():
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < 1.0:
        await asyncio.sleep(1.0 - elapsed)
    _last_request_time = time.monotonic()


async def fetch_has_art(musicbrainz_id: str) -> bool | None:
    """Return True if CAA has front art for this release-group, False if it
    confirmed none (404), or None if the lookup was inconclusive (timeout /
    transient error) so the caller can leave the row unchecked and retry later.

    Hits the lightweight JSON metadata endpoint (no image bytes downloaded).
    """
    await _rate_limit()
    url = f"{BASE_URL}/release-group/{musicbrainz_id}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        if resp.status_code == 404:
            return False
        if resp.status_code == 200:
            data = resp.json()
            images = data.get("images", []) if isinstance(data, dict) else []
            # A group with a designated front image is what we want; fall back
            # to "any image" so we still light up rather than show nothing.
            return any(img.get("front") for img in images) or bool(images)
        logger.info("CAA %s -> HTTP %d (inconclusive)", musicbrainz_id, resp.status_code)
        return None
    except Exception:
        logger.info("CAA lookup failed for %s (inconclusive)", musicbrainz_id, exc_info=True)
        return None


async def ensure_cover_art(musicbrainz_ids) -> dict:
    """Populate the cache for any of the given release-group MBIDs not already
    checked. Opens its own short session per write. Rate-limited; safe to call
    after a resolve / album-charge or from the backfill script.

    Returns {checked, found} counts for this run.
    """
    mbids = [m for m in dict.fromkeys(musicbrainz_ids) if m]  # dedup, drop falsy
    if not mbids:
        return {"checked": 0, "found": 0}

    # Skip MBIDs already in the cache (existence == checked), in one query.
    db = SessionLocal()
    try:
        known = {
            row[0] for row in db.query(MbCoverArt.musicbrainz_id)
            .filter(MbCoverArt.musicbrainz_id.in_(mbids)).all()
        }
    finally:
        db.close()

    todo = [m for m in mbids if m not in known]
    checked = found = 0
    for mbid in todo:
        has_art = await fetch_has_art(mbid)
        if has_art is None:
            continue  # inconclusive -- leave unchecked so a later run retries
        db = SessionLocal()
        try:
            db.merge(MbCoverArt(
                musicbrainz_id=mbid,
                has_art=bool(has_art),
                checked_at=datetime.utcnow(),
            ))
            db.commit()
        finally:
            db.close()
        checked += 1
        if has_art:
            found += 1
    return {"checked": checked, "found": found}
