"""One-shot (Postgres): ensure every calibrated song has a row in song_slugs.

The historical gap: songs ingested purely via chart_reading (daily Billboard
Hot 100 + the streaming charts) wrote a `songs` row but never persisted a URL
slug -- ~467 calibrated songs were slugless. The write chokepoint
(`song_sync.upsert_unified_song`) now persists a slug for every new song; this
backfills the existing gap so the table is complete and the sitemap covers the
whole catalog.

Idempotent -- re-running is a no-op (ensure_song_slug returns the existing slug).
Reuses the exact minter the chokepoint uses, so slugs match the live URLs.

Run against the prod-pointed DATABASE_URL (tunnel locally, or inside the
container on the server):
    python scripts/backfill_song_slugs_pg.py [--dry-run]
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from app.database import SessionLocal
from app.services.song_sync import ensure_song_slug


def main() -> int:
    dry = "--dry-run" in sys.argv
    db = SessionLocal()
    rows = db.execute(text(
        "SELECT s.id, s.title, s.artist FROM songs s "
        "WHERE s.rubric_color IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM song_slugs ss WHERE ss.song_id = s.id) "
        "ORDER BY s.id"
    )).all()
    print(f"calibrated songs missing a slug: {len(rows)}")
    if dry:
        for sid, title, artist in rows[:20]:
            print(f"  would slug #{sid}: {title} -- {artist}")
        print("(dry run -- no writes)")
        return 0

    made = 0
    for sid, title, artist in rows:
        slug = ensure_song_slug(db, sid, title or "", artist or "")
        if slug:
            made += 1
        # Commit per row so a mid-run failure keeps prior work (idempotent anyway).
        db.commit()
    print(f"backfill complete: {made} slugs ensured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
