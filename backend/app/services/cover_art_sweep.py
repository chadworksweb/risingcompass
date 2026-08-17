"""The automated cover-art lane: resolve what has no art yet, on a schedule.

BEFORE THIS, NOTHING WAS AUTOMATIC. Song-level cover art lived only in
`scripts/backfill_song_cover_art.py`, run by hand in chunks, and release-level
MBIDs were only ever set as a side effect of a MusicBrainz catalogue resolve or
an Album Charger run. A song calibrated today, or a release created by hand for a
terminal album read, sat without art until somebody remembered to run something.

WHY A CRON AND NOT AN INLINE CALL. Resolving one song costs up to two MusicBrainz
searches plus eight lookups, and MusicBrainz is 1 req/sec and 503s freely under
load. That is minutes of network per album, so it can NEVER run inside a
calibration write -- `search_recording_release_group` is marked offline-only for
exactly this reason. Automatic here means "runs on a schedule without anyone
remembering", not "runs during the request".

EVERY PASS IS BOUNDED. Each run takes a slice and leaves the rest for the next
one. A backlog drains over several nights instead of hammering MusicBrainz once,
and a failure costs one slice rather than a whole catalogue.

Ordering matters: RELEASES resolve before songs, because `_pending_songs` skips
any song whose release already supplies art. Attaching a release's MBID first
therefore removes its whole tracklist from the song queue in one lookup instead
of N.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app.database import SessionLocal
from app.models import MbCoverArt, Release, ReleaseSong, Song
from app.services import coverart, musicbrainz, release_mbid

logger = logging.getLogger(__name__)

# Per-run caps. Deliberately small: at ~2s per song this keeps a nightly pass to
# a couple of minutes of MusicBrainz traffic.
DEFAULT_RELEASE_LIMIT = 15
DEFAULT_SONG_LIMIT = 60

# Consecutive song misses that end a run. See the note at the break below: a
# miss and a MusicBrainz outage are indistinguishable, and a miss is stamped
# permanently, so a solid run of them is treated as the API being down.
MISS_RUN_ABORT = 8


def pending_songs(limit: int | None, recheck_misses: bool = False) -> list[tuple[int, str, str]]:
    """Songs with no resolvable cover art yet, newest first.

    Newest-first because the songs most likely to be looked at are the ones that
    just charted, and this runs in chunks rather than to completion.

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
        linked: dict[int, str] = {}
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
            q = q.filter(Song.release_group_mbid.is_(None))
        else:
            q = q.filter(Song.release_group_checked_at.is_(None))
        rows = q.order_by(Song.id.desc()).all()

        pending = [
            (sid, title, artist) for (sid, title, artist) in rows
            if linked.get(sid) not in art_mbids
            and (title or "").strip() and (artist or "").strip()
        ]
    finally:
        db.close()
    return pending[:limit] if limit is not None else pending


def orphan_mbids() -> list[str]:
    """Resolved release-group MBIDs that were never checked against CAA.

    The CAA pass runs after the resolve loop, so an interrupted run strands every
    MBID it had resolved: the song carries an mbid AND a checked_at, so
    `pending_songs` skips it forever and its art never appears. Sweeping these
    FIRST makes every later run self-healing.
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


def rejected_mbids() -> dict[int, set[str]]:
    """song_id -> release groups a reader reported and an admin confirmed wrong.

    A confirm clears the song's mbid but LEAVES release_group_checked_at set, so
    the ordinary pass already skips the song. This matters on the deliberate
    retry, which is the pass that would otherwise walk straight back to the
    rejected cover and re-pick it -- a correction undone by the next automated
    run, the way hand-deleted releases were before migration 147.
    """
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT song_id, reported_mbid FROM song_cover_art_reports "
            "WHERE status = 'confirmed' AND reported_mbid IS NOT NULL"
        )).fetchall()
    except Exception:
        # Not load-bearing for a resolve: a pass that cannot read it should still
        # run, just without the exclusions.
        logger.warning("Could not read cover-art rejections; running without them",
                       exc_info=True)
        return {}
    finally:
        db.close()
    out: dict[int, set[str]] = {}
    for song_id, mbid in rows:
        out.setdefault(song_id, set()).add(mbid)
    return out


def stamp_song(song_id: int, mbid: str | None, rg_date: str | None = None) -> None:
    """Record the resolve outcome. Own short session per write, like the CAA cache.

    A stamped `checked_at` with a NULL mbid is a recorded MISS, which is what
    stops the sweep re-searching the same song every night.
    """
    db = SessionLocal()
    try:
        db.query(Song).filter(Song.id == song_id).update(
            {
                "release_group_mbid": mbid,
                "release_group_date": rg_date or None,
                "release_group_checked_at": datetime.now(timezone.utc),
            },
            synchronize_session=False,
        )
        db.commit()
    finally:
        db.close()


async def sweep_songs(limit: int | None = DEFAULT_SONG_LIMIT,
                      recheck_misses: bool = False,
                      on_row=None) -> dict:
    """Resolve a bounded batch of songs to a release group, then warm CAA.

    `on_row(i, total, title, artist, hit)` lets the terminal script print progress
    without this module knowing anything about a console.
    """
    stats = {"recovered": 0, "checked": 0, "matched": 0, "no_match": 0, "found_art": 0}

    orphans = orphan_mbids()
    if orphans:
        res = await coverart.ensure_cover_art(orphans)
        stats["recovered"] = res.get("checked", 0)

    pending = pending_songs(limit, recheck_misses)
    if not pending:
        return stats

    rejected = rejected_mbids()
    found: list[str] = []
    consecutive_misses = 0
    for i, (song_id, title, artist) in enumerate(pending, 1):
        try:
            hit = await musicbrainz.search_recording_release_group(
                artist, title, exclude_mbids=rejected.get(song_id),
            )
        except Exception:
            # Never let one bad row end a long run.
            logger.exception("Song cover-art resolve failed for %s (%s)", song_id, title)
            continue
        stats["checked"] += 1
        mbid = hit["mbid"] if hit else None
        stamp_song(song_id, mbid, hit.get("first_release_date") if hit else None)
        if mbid:
            stats["matched"] += 1
            found.append(mbid)
            consecutive_misses = 0
        else:
            stats["no_match"] += 1
            consecutive_misses += 1
        if on_row:
            on_row(i, len(pending), title, artist, hit)

        # STOP ON A RUN OF MISSES, because a miss and an outage look identical.
        # `_mb_get` swallows its errors and returns None, so
        # `search_recording_release_group` cannot tell "searched, found nothing"
        # from "could not search" -- and a miss STAMPS checked_at, which is
        # permanent until someone runs --recheck-misses. Hand-run that was safe:
        # the operator watched 503s scroll past. Automated nightly it would burn
        # a whole batch into recorded misses every time MusicBrainz wobbled.
        # A genuine no-match tail is mixed; a solid run of them is the API being
        # down. Stopping costs one slice, which the next run picks up anyway.
        if consecutive_misses >= MISS_RUN_ABORT:
            stats["aborted_on_miss_run"] = True
            logger.warning(
                "Song cover-art sweep stopped after %s consecutive misses -- "
                "treating it as a MusicBrainz outage rather than stamping the rest",
                consecutive_misses,
            )
            break

    if found:
        res = await coverart.ensure_cover_art(found)
        stats["found_art"] = res.get("found", 0)
    return stats


async def run_sweep(release_limit: int = DEFAULT_RELEASE_LIMIT,
                    song_limit: int = DEFAULT_SONG_LIMIT) -> dict:
    """One automated pass: releases first, then songs. Both bounded, both fail-soft."""
    releases = await release_mbid.resolve_pending(limit=release_limit)
    songs = await sweep_songs(limit=song_limit)
    summary = {"releases": releases, "songs": songs}
    logger.info("Cover art sweep: %s", summary)
    return summary
