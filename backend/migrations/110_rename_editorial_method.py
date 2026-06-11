"""Rename the 'editorial' calibration method to 'catalog_backfill'.

'editorial' was a fossil label from the pre-unification four-table model. In
the unified corpus it actually marks authoritative non-chart catalog readings
(album / library backfill + agent-curated), NOT an editorial workflow --
Rising Compass holds no editorial content. Rename the stored value everywhere
so the provenance is honest.

The method stays authoritative under the new name (it sits in the auth-method
sets in calibrator.py / song_sync.py as 'catalog_backfill'), so this re-stamp
does NOT change any reading or its override-protection -- it only renames.

Idempotent: re-running finds no 'editorial' rows. PG-compatible (063+).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "UPDATE songs SET canonical_calibration_method = 'catalog_backfill' "
        "WHERE canonical_calibration_method = 'editorial'"
    ))
    conn.execute(text(
        "UPDATE song_ingestions SET method = 'catalog_backfill' "
        "WHERE method = 'editorial'"
    ))
