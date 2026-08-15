"""Stamp release_group_date on songs resolved BEFORE migration 151.

WHY THIS EXISTS. The date audit (scripts/audit_song_cover_art.py) can only see a
song whose pick carries a stored date, and only the backfill's `_stamp` writes
one. Every song resolved before migration 151 therefore sits outside the audit
entirely -- 1,180 rows on the day this was written, which was the MAJORITY of the
songs showing art. Whatever wrong picks are in that population are invisible, not
absent.

WHY NOT JUST RE-RESOLVE THEM. A re-resolve costs up to 2 searches plus 8 lookups
per SONG and re-litigates picks that are probably fine, at roughly 10 seconds
each. This asks a narrower question -- "when was the group I already chose first
issued?" -- which is ONE lookup per RELEASE GROUP. The population shares groups
heavily (1,180 songs across 968 groups), and albums collapse hardest of all, so
the cost falls with exactly the songs most likely to share a cover. It also
touches no pick: every existing release_group_mbid survives untouched, so nothing
that currently renders can change as a result of running this.

FAILURE IS NOT AN ANSWER. MusicBrainz returning no date and MusicBrainz failing
to answer are different facts, and get_release_group_date keeps them apart ("" vs
None). Only a real answer is stored. A failed lookup leaves the song exactly as
it was, so a later run retries it -- storing "" on a timeout would permanently
retire the song from the audit having never actually checked it.

IDEMPOTENT and interruptible: each group is written as it resolves, and the
pending set is recomputed from the rows that still lack a date, so a re-run picks
up exactly where the last one stopped.

Usage:
    cd backend
    .venv\\Scripts\\python.exe scripts\\backfill_release_group_dates.py [--limit N]

Then run scripts/audit_song_cover_art.py, which will now see the whole Library.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.database import SessionLocal
from app.services import musicbrainz

# Songs that HAVE a pick but no date on it. Ordered by how many songs each group
# serves, so an interrupted run has already bought the widest coverage.
_PENDING_SQL = """
SELECT release_group_mbid, COUNT(*) AS song_count
FROM songs
WHERE release_group_mbid IS NOT NULL
  AND release_group_date IS NULL
GROUP BY release_group_mbid
ORDER BY song_count DESC
"""


def _pending_groups(limit: int | None) -> list[tuple[str, int]]:
    db = SessionLocal()
    try:
        rows = db.execute(text(_PENDING_SQL)).fetchall()
    finally:
        db.close()
    groups = [(r[0], r[1]) for r in rows if r[0]]
    return groups[:limit] if limit is not None else groups


def _stamp_group(mbid: str, rg_date: str) -> int:
    """Write the date onto every song carrying this pick. Returns rows touched.

    Scoped to rows that still lack a date so this can never overwrite a date the
    resolver itself stamped -- that one was captured at pick time and is the more
    authoritative of the two.
    """
    db = SessionLocal()
    try:
        result = db.execute(
            text("UPDATE songs SET release_group_date = :d "
                 "WHERE release_group_mbid = :m AND release_group_date IS NULL"),
            {"d": rg_date, "m": mbid},
        )
        db.commit()
        return result.rowcount or 0
    finally:
        db.close()


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Fetch first-release-date for already-picked release groups.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap the number of release groups fetched this run.")
    args = ap.parse_args()

    groups = _pending_groups(args.limit)
    if not groups:
        print("Every resolved song already carries a release-group date.")
        return

    songs = sum(n for _, n in groups)
    print(f"Fetching {len(groups)} release group(s) covering {songs} song(s) "
          f"(~1/sec, est. {len(groups)}s)...")

    dated = undated = failed = 0
    songs_stamped = 0
    for i, (mbid, song_count) in enumerate(groups, 1):
        try:
            rg_date = await musicbrainz.get_release_group_date(mbid)
        except Exception as exc:  # never let one bad group end a long run
            print(f"  [{i}/{len(groups)}] ERROR {mbid}: {exc}")
            failed += 1
            continue

        if rg_date is None:
            # Lookup failed. Leave the rows alone so a later run retries them.
            failed += 1
            print(f"  [{i}/{len(groups)}] {mbid} -> lookup failed, left for a retry")
            continue

        if not rg_date:
            # MB genuinely has no date. Nothing to store, and nothing to retry --
            # these stay unauditable, correctly.
            undated += 1
            continue

        n = _stamp_group(mbid, rg_date)
        songs_stamped += n
        dated += 1
        if i % 25 == 0 or n > 3:
            print(f"  [{i}/{len(groups)}] {mbid} -> {rg_date} ({n} song(s))")

    print(f"\nDone. groups dated={dated} no_date_in_mb={undated} failed={failed}")
    print(f"Songs stamped: {songs_stamped}")
    print("Run scripts/audit_song_cover_art.py to audit the newly-datable rows.")


if __name__ == "__main__":
    asyncio.run(main())
