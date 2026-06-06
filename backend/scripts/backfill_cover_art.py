"""Backfill the Cover Art Archive cache (mb_cover_art) for existing releases.

For every distinct releases.musicbrainz_id (release-group MBID) not already in
mb_cover_art, query CAA once and record whether art exists. Keyed by MBID, so it
survives release-row rebuilds and never re-queries a checked group.

Rate-limited to 1 req/sec (handled in services/coverart). Idempotent: re-running
only checks MBIDs still absent from the cache (e.g. ones added since, or earlier
inconclusive timeouts that were left unchecked on purpose).

Usage:
    cd backend
    .venv\\Scripts\\python.exe scripts\\backfill_cover_art.py [--limit N]
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

from app.database import SessionLocal
from app.models import MbCoverArt, Release
from app.services import coverart


def _pending_mbids(limit: int | None) -> list[str]:
    """Distinct release-group MBIDs on releases that aren't cached yet."""
    db = SessionLocal()
    try:
        cached = {row[0] for row in db.query(MbCoverArt.musicbrainz_id).all()}
        rows = (
            db.query(Release.musicbrainz_id)
            .filter(Release.musicbrainz_id.isnot(None))
            .distinct()
            .all()
        )
        pending = [r[0] for r in rows if r[0] and r[0] not in cached]
    finally:
        db.close()
    if limit is not None:
        pending = pending[:limit]
    return pending


async def main():
    parser = argparse.ArgumentParser(description="Backfill CAA cover-art cache.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap the number of MBIDs checked this run.")
    args = parser.parse_args()

    pending = _pending_mbids(args.limit)
    if not pending:
        print("Cover-art cache up to date -- no uncached release-group MBIDs.")
        return

    print(f"Checking {len(pending)} release-group MBID(s) against CAA "
          f"(~1/sec, est. {len(pending)}s)...")
    result = await coverart.ensure_cover_art(pending)
    inconclusive = len(pending) - result["checked"]
    print(
        f"Done. checked={result['checked']} found_art={result['found']} "
        f"none={result['checked'] - result['found']} inconclusive={inconclusive}"
    )
    if inconclusive:
        print("  (inconclusive MBIDs left uncached -- re-run later to retry.)")


if __name__ == "__main__":
    asyncio.run(main())
