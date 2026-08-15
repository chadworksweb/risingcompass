"""Backfill SONG-level cover art (release_group_mbid -> mb_cover_art -> CAA).

Releases have had Cover Art Archive art since migration 091, but that only ever
reached songs with a ReleaseSong link. Chart-born and Lyrical-Charger-born songs
-- the majority of the Library -- have no Release row, so their song pages showed
no art. This resolves each such song to its own release-GROUP MBID via
MusicBrainz's recording index, then fills the same mb_cover_art cache the release
path already uses.

Cover Art Archive is the ONLY art source. Apple's iTunes Search API was evaluated
and rejected: its Promo Content grant requires a "Download on iTunes" badge
proximate to every image, on a page that promotes the content. A critical reading
is not a promotion, and RC ships no store badge.

SPEED: two throttled lookups per song (MusicBrainz 1/sec, then CAA 1/sec), so
budget ~2s per song. This is why the resolve is offline and never on the request
path. Run it in chunks with --limit; there is no deadline and no cron.

IDEMPOTENT: a song is checked at most once. `release_group_checked_at` is stamped
whether or not a match was found, so a recorded miss ("MusicBrainz has no
confident match") is never re-searched. Re-running only picks up songs added
since the last pass. Use --recheck-misses to deliberately retry recorded misses
(worth doing occasionally -- MusicBrainz gains entries for new songs over time).

Usage:
    cd backend
    .venv\\Scripts\\python.exe scripts\\backfill_song_cover_art.py [--limit N]
    .venv\\Scripts\\python.exe scripts\\backfill_song_cover_art.py --limit 50 --recheck-misses
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.database import SessionLocal
from app.models import MbCoverArt, Release, ReleaseSong, Song
from app.services import coverart, musicbrainz


def _pending_songs(limit: int | None, recheck_misses: bool) -> list[tuple[int, str, str]]:
    """Songs with no resolvable cover art yet, newest first.

    Newest-first because the songs most likely to be looked at are the ones that
    just charted, and this script is run in chunks rather than to completion.

    Excludes songs whose Release already supplies art -- the song page reads that
    release's MBID directly, so resolving a second one for them would be wasted
    lookups and could disagree with the release page.
    """
    db = SessionLocal()
    try:
        art_mbids = {
            row[0] for row in db.query(MbCoverArt.musicbrainz_id)
            .filter(MbCoverArt.has_art.is_(True)).all()
        }
        # song_id -> release MBID, for songs that have a release link at all.
        linked = {}
        for song_id, mbid in (
            db.query(ReleaseSong.song_id, Release.musicbrainz_id)
            .join(Release, Release.id == ReleaseSong.release_id)
            .filter(ReleaseSong.song_id.isnot(None))
            .all()
        ):
            if mbid and (song_id not in linked or linked[song_id] not in art_mbids):
                linked[song_id] = mbid

        q = db.query(Song.id, Song.title, Song.artist)
        if recheck_misses:
            # Retry recorded misses, but never re-resolve a song that already
            # landed an MBID.
            q = q.filter(Song.release_group_mbid.is_(None))
        else:
            q = q.filter(Song.release_group_checked_at.is_(None))
        rows = q.order_by(Song.id.desc()).all()

        pending = [
            (sid, title, artist) for (sid, title, artist) in rows
            # A song whose release already has art needs nothing.
            if linked.get(sid) not in art_mbids
            and (title or "").strip() and (artist or "").strip()
        ]
    finally:
        db.close()
    if limit is not None:
        pending = pending[:limit]
    return pending


def _orphan_mbids() -> list[str]:
    """Resolved release-group MBIDs that were never checked against CAA.

    The CAA pass runs after the whole resolve loop, so interrupting a run (Ctrl-C,
    a closed laptop) strands every MBID it had resolved: the song carries an mbid
    AND a checked_at, so _pending_songs skips it forever and its art never
    appears. Sweeping these FIRST makes any later run self-healing.
    """
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT DISTINCT s.release_group_mbid FROM songs s "
            "LEFT JOIN mb_cover_art m ON m.musicbrainz_id = s.release_group_mbid "
            "WHERE s.release_group_mbid IS NOT NULL AND m.musicbrainz_id IS NULL"
        )).fetchall()
        return [r[0] for r in rows if r[0]]
    finally:
        db.close()


def _rejected_mbids() -> dict[int, set[str]]:
    """song_id -> release groups a reader reported and an admin confirmed wrong.

    A confirm clears the song's mbid but LEAVES release_group_checked_at set, so
    the ordinary backfill already skips the song and the pulled art stays pulled.
    This matters on the deliberate retry (--recheck-misses), which is exactly the
    pass that would otherwise walk straight back to the rejected cover and re-pick
    it -- a correction undone by the next automated run, the same way hand-deleted
    releases were before migration 147.
    """
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT song_id, reported_mbid FROM song_cover_art_reports "
            "WHERE status = 'confirmed' AND reported_mbid IS NOT NULL"
        )).fetchall()
    except Exception as exc:
        # The table is not load-bearing for a resolve. A pass that can't read it
        # should still run, just without the exclusions.
        print(f"  (could not read cover-art rejections: {exc})")
        return {}
    finally:
        db.close()
    out: dict[int, set[str]] = {}
    for song_id, mbid in rows:
        out.setdefault(song_id, set()).add(mbid)
    return out


def _stamp(song_id: int, mbid: str | None, rg_date: str | None = None) -> None:
    """Record the resolve outcome. Own short session per write, like the CAA cache.

    `rg_date` is MB's first-release-date for the picked release group. It is stored
    (migration 151) so a bad pick is detectable in bulk afterwards: the artist-credit
    check catches a wrong artist, but a right artist on an archival set or reissue
    only shows up against the calendar. See scripts/audit_song_cover_art.py.
    """
    db = SessionLocal()
    try:
        db.query(Song).filter(Song.id == song_id).update(
            {
                "release_group_mbid": mbid,
                "release_group_date": rg_date or None,
                "release_group_checked_at": datetime.utcnow(),
            },
            synchronize_session=False,
        )
        db.commit()
    finally:
        db.close()


async def main():
    parser = argparse.ArgumentParser(description="Backfill song-level CAA cover art.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap the number of songs resolved this run.")
    parser.add_argument("--recheck-misses", action="store_true",
                        help="Retry songs previously recorded as no-match.")
    args = parser.parse_args()

    # Bank any stranded work from a previous interrupted run before doing more.
    orphans = _orphan_mbids()
    if orphans:
        print(f"Recovering {len(orphans)} resolved MBID(s) never checked against "
              f"CAA (~1/sec, est. {len(orphans)}s)...")
        res = await coverart.ensure_cover_art(orphans)
        print(f"  recovered: checked={res['checked']} found_art={res['found']}\n")

    pending = _pending_songs(args.limit, args.recheck_misses)
    if not pending:
        print("Song cover art up to date -- no unresolved songs.")
        return

    print(f"Resolving {len(pending)} song(s) against MusicBrainz "
          f"(~2s each incl. the CAA check, est. {len(pending) * 2}s)...")

    rejected = _rejected_mbids()
    if rejected:
        total = sum(len(v) for v in rejected.values())
        print(f"  ({total} reader-rejected release group(s) across "
              f"{len(rejected)} song(s) will be skipped.)")

    matched, missed = 0, 0
    found_mbids: list[str] = []
    for i, (song_id, title, artist) in enumerate(pending, 1):
        try:
            hit = await musicbrainz.search_recording_release_group(
                artist, title, exclude_mbids=rejected.get(song_id),
            )
        except Exception as exc:  # never let one bad row end a long run
            print(f"  [{i}/{len(pending)}] ERROR {title} - {artist}: {exc}")
            continue

        mbid = hit["mbid"] if hit else None
        _stamp(song_id, mbid, hit.get("first_release_date") if hit else None)
        if mbid:
            matched += 1
            found_mbids.append(mbid)
            print(f"  [{i}/{len(pending)}] {title} - {artist} -> "
                  f"{hit['release_group_title']} ({hit['primary_type'] or 'release'})")
        else:
            missed += 1
            print(f"  [{i}/{len(pending)}] {title} - {artist} -> no confident match")

    print(f"\nResolved: matched={matched} no_match={missed}")

    if found_mbids:
        print(f"Checking {len(set(found_mbids))} release-group(s) against CAA (~1/sec)...")
        result = await coverart.ensure_cover_art(found_mbids)
        print(f"Done. checked={result['checked']} found_art={result['found']} "
              f"none={result['checked'] - result['found']}")
        print("  (MBIDs already cached from the release path were skipped.)")
    else:
        print("No new release-groups to check against CAA.")


if __name__ == "__main__":
    asyncio.run(main())
