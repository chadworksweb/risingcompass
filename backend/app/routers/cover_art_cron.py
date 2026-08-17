"""Cover-art sweep -- cron entry point.

POST /api/admin/agent/cron/cover-art runs one bounded pass: resolve release-group
MBIDs for releases that have none (which is what unlocks their cover art), then
resolve song-level cover art for songs never checked, then warm the Cover Art
Archive cache for anything new.

Until this existed nothing was automatic. Song cover art was a hand-run chunked
script and release MBIDs were only ever a side effect of a MusicBrainz catalogue
resolve or an Album Charger run, so a release created by hand for a terminal
album read could never show art at all.

Auth reuses the daily-reading cron lane (`X-Reading-Cron-Key` ==
RC_READING_CRON_KEY), like the LEIT sweep -- no new server secret, and it runs in
the same nightly lane. The work lives in `services/cover_art_sweep.py`; the same
functions back `scripts/backfill_song_cover_art.py` and
`scripts/backfill_release_mbid.py` so the manual and automatic lanes cannot drift.
"""

import logging

from fastapi import APIRouter, Depends, Query

from app.auth import verify_reading_cron_key
from app.services import cover_art_sweep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/agent", tags=["cover-art"])


@router.post("/cron/cover-art", dependencies=[Depends(verify_reading_cron_key)])
async def cron_cover_art(
    release_limit: int = Query(cover_art_sweep.DEFAULT_RELEASE_LIMIT, ge=0, le=200),
    song_limit: int = Query(cover_art_sweep.DEFAULT_SONG_LIMIT, ge=0, le=500),
):
    """Service endpoint for the nightly cover-art sweep.

    Bounded by design: MusicBrainz is 1 req/sec, so each run takes a slice and
    leaves the rest for tomorrow. The caps are overridable for a one-off catch-up
    pass, but the ceiling keeps any single run from turning into an outage.
    """
    return await cover_art_sweep.run_sweep(
        release_limit=release_limit, song_limit=song_limit,
    )
