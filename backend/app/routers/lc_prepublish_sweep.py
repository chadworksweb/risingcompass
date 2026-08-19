"""POST /api/admin/agent/cron/lc-prepublish-sweep -- publish held readings.

The "silence means accepted" half of the Lyrical Charger contest lane. A reader
who is happy with their reading almost never clicks anything, so a held reading
that expired into DISCARD would quietly throw away most of the Charger's intake:
the feature would look like it was working while the Library starved. The sweep
publishes instead, and only an explicit contest interrupts it.

It also closes the contested-then-abandoned case. A reader who contests and then
leaves has a held RE-READ row, and that is what publishes: the more considered of
the two readings, and admin already has the email either way.

CADENCE IS MINUTES, NOT NIGHTS. Every other cron in this lane is daily, and this
one must not be: HOLD_TTL is 30 minutes, so a nightly run would leave a day of
readings invisible to the Library and to the run-cap check. Every 10 minutes.

Auth reuses the daily-reading cron lane (`X-Reading-Cron-Key`), so no new server
secret is needed. Environment-filtered inside `sweep_expired` -- local dev shares
the prod database through the tunnel, and an unfiltered sweep from a laptop would
publish prod readings.

A no-op run is the normal result and returns 200 with zeroes. It is a no-op
entirely when `lc_prepublish.enabled` is off, because nothing is ever held.
"""

import logging

from fastapi import APIRouter, Depends

from app.auth import verify_reading_cron_key
from app.database import SessionLocal
from app.services import lc_publish

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/agent", tags=["lc-prepublish-sweep"])


@router.post("/cron/lc-prepublish-sweep",
             dependencies=[Depends(verify_reading_cron_key)])
async def cron_lc_prepublish_sweep():
    """Publish every reading held past the TTL. Returns the counts."""
    db = SessionLocal()
    try:
        return lc_publish.sweep_expired(db)
    finally:
        db.close()
