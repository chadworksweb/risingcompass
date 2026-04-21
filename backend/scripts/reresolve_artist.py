"""One-shot: wipe an artist's releases + release_songs, then re-resolve via
the updated MusicBrainz filter. Local-use script — hits the Turso primary
directly for the wipe, then calls resolve_artist_releases() in-process so
we bypass curl's timeout on long MB fetches.

Usage:
    .venv/Scripts/python.exe scripts/reresolve_artist.py the-beatles bad-bunny
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from app.database import SessionLocal  # noqa: E402
from app.models import Artist, Release, ReleaseSong  # noqa: E402
from app.services.artist_utils import resolve_artist_releases  # noqa: E402


async def wipe_and_resolve(slug: str) -> None:
    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == slug).first()
        if not artist:
            print(f"[{slug}] not found")
            return
        artist_id = artist.id
        release_ids = [r.id for r in db.query(Release).filter(Release.artist_id == artist_id).all()]
        rs_count = 0
        if release_ids:
            rs_count = db.query(ReleaseSong).filter(ReleaseSong.release_id.in_(release_ids)).delete(synchronize_session=False)
        rel_count = db.query(Release).filter(Release.artist_id == artist_id).delete(synchronize_session=False)
        db.commit()
        print(f"[{slug}] wiped {rel_count} releases, {rs_count} release_songs")
    finally:
        db.close()

    print(f"[{slug}] resolving via MusicBrainz (this takes minutes)…")
    stats = await resolve_artist_releases(artist_id)
    print(f"[{slug}] {stats}")


async def main() -> int:
    slugs = sys.argv[1:]
    if not slugs:
        print("usage: reresolve_artist.py <slug> [slug …]")
        return 1
    for slug in slugs:
        await wipe_and_resolve(slug)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
