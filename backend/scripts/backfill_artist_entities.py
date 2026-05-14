"""Backfill Artist rows (+ song_artists credits) for calibrated songs.

For every distinct primary-artist name across compass_songs, library_songs,
and submitted_songs (calibrated rows only) that has no matching Artist row,
this script:

  1. Creates an Artist (with collision-safe slug via generate_artist_slug).
  2. Adds song_artists rows linking every matching song to that Artist with
     role='primary' so the artist page surfaces a non-empty catalog.

Multi-artist credits ("Justin Bieber, Nicki Minaj") collapse to the primary
via normalize_artist_name. Featured artists are out of scope for this pass.

Idempotent: re-running skips any name already present in artists.name (case
insensitive) and any song already linked.

Usage:
    cd backend
    .venv\\Scripts\\python.exe scripts\\backfill_artist_entities.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from sqlalchemy import func

from app.database import SessionLocal
from app.models import Artist, CompassSong, LibrarySong, SongArtist, SubmittedSong
from app.services.artist_utils import generate_artist_slug, normalize_artist_name


SOURCES = [
    ("compass", CompassSong),
    ("library", LibrarySong),
    ("submitted", SubmittedSong),
]


def _collect_song_rows(db) -> list[tuple[str, int, str]]:
    """Return (source, song_id, raw_artist_string) for every calibrated song
    whose artist string is non-empty."""
    out: list[tuple[str, int, str]] = []
    for source, Model in SOURCES:
        q = (
            db.query(Model.id, Model.artist)
            .filter(Model.charge_value.isnot(None))
            .filter(Model.artist.isnot(None))
        )
        for song_id, raw_artist in q.all():
            raw = (raw_artist or "").strip()
            if not raw:
                continue
            out.append((source, song_id, raw))
    return out


def _existing_artist_names(db) -> set[str]:
    """Lowercased set of artist names already present."""
    return {
        name.lower()
        for (name,) in db.query(Artist.name).filter(Artist.name.isnot(None)).all()
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Report what would change without writing.")
    args = p.parse_args()

    db = SessionLocal()
    try:
        rows = _collect_song_rows(db)
        existing_lower = _existing_artist_names(db)

        # Group songs by their primary artist.
        # Map: primary_display_name -> list of (source, song_id)
        groups: dict[str, list[tuple[str, int]]] = {}
        display_for_lower: dict[str, str] = {}
        for source, song_id, raw_artist in rows:
            primary = normalize_artist_name(raw_artist)
            if not primary:
                continue
            lower = primary.lower()
            if lower in existing_lower:
                continue
            # First seen casing wins — minor display nuance.
            display_for_lower.setdefault(lower, primary)
            groups.setdefault(display_for_lower[lower], []).append((source, song_id))

        if not groups:
            print("Nothing to backfill — every calibrated artist already has an Artist row.")
            return 0

        print(f"Found {len(groups)} new artist(s) to create.")
        if args.dry_run:
            for name, songs in sorted(groups.items()):
                print(f"  DRY  {name!r}  ({len(songs)} song link(s))")
            return 0

        created_artists = 0
        created_credits = 0
        for name, song_keys in sorted(groups.items()):
            slug = generate_artist_slug(name, db)
            artist = Artist(name=name, slug=slug)
            db.add(artist)
            db.flush()  # populate artist.id
            created_artists += 1

            for source, song_id in song_keys:
                # UniqueConstraint guarantees no duplicates; existence check
                # for an already-present row (defense in depth, since artist
                # is new this run it shouldn't matter).
                exists = (
                    db.query(SongArtist.id)
                    .filter(SongArtist.song_source == source)
                    .filter(SongArtist.song_id == song_id)
                    .filter(SongArtist.artist_id == artist.id)
                    .first()
                )
                if exists:
                    continue
                db.add(SongArtist(
                    song_source=source,
                    song_id=song_id,
                    artist_id=artist.id,
                    role="primary",
                    position=0,
                ))
                created_credits += 1
            print(f"  OK   {name!r:<48}  slug={slug:<48}  songs={len(song_keys)}")

        db.commit()
        print()
        print(f"Created {created_artists} artist(s); {created_credits} song_artists credit(s).")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
