"""Attach a release-group MBID to a release, which is what unlocks its cover art.

WHY THIS EXISTS. Cover art is derived entirely from `releases.musicbrainz_id`
(-> `mb_cover_art` -> coverartarchive.org/release-group/{mbid}/front-500). Two
lanes set that MBID today: `resolve_artist_releases`, for releases MusicBrainz
itself produced, and the Album Charger, which searches MB for the album a user
pasted. A release created BY HAND for a terminal v3 album read gets neither, so
every release carrying a lens reading had a NULL MBID and could never show art.

WHAT IT DOES NOT DO. It never overwrites an MBID that is already set, and it
never guesses. The confidence rule is the Album Charger's, unchanged: a high
score, an exact title-slug match, and a clear margin over the runner-up. Anything
short of that resolves to nothing, because **no art beats wrong art** -- the same
posture `_pick_release_group` takes on the song side, for the same reason. A
wrong cover is a claim about what a reading is about.

OFFLINE ONLY. Every resolve costs a MusicBrainz search at 1 req/sec plus a Cover
Art Archive check. NEVER call this from a request; it runs from the cron lane and
the terminal script.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from app.database import SessionLocal
from app.models import Release
from app.services import coverart, musicbrainz
from app.services.artist_utils import slugify

logger = logging.getLogger(__name__)

# The Album Charger's thresholds, and now the single owner of them: the charger
# imports these rather than keeping its own copy.
MB_MIN_SCORE = 92
MB_MARGIN = 12


def pick_mb_match(submitted_title: str, candidates: list[dict]):
    """Return (chosen_mbid, needs_pick, top_candidates).

    chosen + not needs_pick -> confident auto-attach.
    none   + needs_pick     -> ambiguous; a human picks.
    none   + not needs_pick -> no candidates at all (no cover).
    """
    if not candidates:
        return (None, False, [])
    top = candidates[0]
    tslug = slugify(submitted_title)
    title_match = bool(tslug) and slugify(top.get("title", "")) == tslug
    margin_ok = len(candidates) == 1 or (top["score"] - candidates[1]["score"]) >= MB_MARGIN
    if top["score"] >= MB_MIN_SCORE and title_match and margin_ok:
        return (top["mbid"], False, candidates[:6])
    return (None, True, candidates[:6])


def _track_mbid_hint(db, release_id: int) -> str | None:
    """The release-group MBID the release's own TRACKS already resolved to, when
    they agree and it is not already claimed by another release.

    Free (one indexed query, no network) and often decisive: the song-level cover
    art backfill has already resolved many of these tracks, and a track usually
    resolves to the album it is on. Release 1932's MBID was sitting on track 3
    the whole time.

    It is a HINT, not an answer. A track can legitimately resolve to a single or
    a compilation instead of the album, so this only wins when it agrees with the
    MusicBrainz search, or when it is corroborated by more than one track.
    """
    rows = db.execute(text(
        "SELECT s.release_group_mbid AS m, COUNT(*) AS n "
        "FROM release_songs rs JOIN songs s ON s.id = rs.song_id "
        "WHERE rs.release_id = :rid AND s.release_group_mbid IS NOT NULL "
        "GROUP BY s.release_group_mbid ORDER BY n DESC"
    ), {"rid": release_id}).fetchall()
    if not rows:
        return None
    top = rows[0]
    # A lone dissenting track proves nothing; two or more agreeing does.
    if top.n < 2:
        return None
    # Never hand a release an MBID another release already owns -- that would put
    # the same cover on two different records.
    taken = db.execute(text(
        "SELECT 1 FROM releases WHERE musicbrainz_id = :m AND id <> :rid LIMIT 1"
    ), {"m": top.m, "rid": release_id}).first()
    return None if taken else top.m


async def resolve_release_mbid(release_id: int) -> dict:
    """Resolve ONE release to a release-group MBID and warm its cover art.

    Returns {release_id, title, status, musicbrainz_id, has_art}. `status` is one
    of: `attached`, `already_set`, `ambiguous`, `not_found`, `missing`.
    """
    out = {"release_id": release_id, "title": None, "status": "missing",
           "musicbrainz_id": None, "has_art": None}

    db = SessionLocal()
    try:
        rel = db.get(Release, release_id)
        if rel is None:
            return out
        out["title"] = rel.title
        if rel.musicbrainz_id:
            out["status"] = "already_set"
            out["musicbrainz_id"] = rel.musicbrainz_id
            return out
        artist_name = db.execute(
            text("SELECT name FROM artists WHERE id = :a"), {"a": rel.artist_id}
        ).scalar()
        title = rel.title
        hint = _track_mbid_hint(db, release_id)
    finally:
        db.close()

    if not artist_name or not title:
        out["status"] = "not_found"
        return out

    candidates = await musicbrainz.search_release_group(artist_name, title)
    chosen, needs_pick, _top = pick_mb_match(title, candidates)

    # The hint only ever CONFIRMS. If the search was ambiguous but the tracks
    # already agree on a group the search also returned, take it -- two
    # independent resolutions landing on the same group is the evidence the
    # margin rule was looking for.
    if not chosen and hint and any(c["mbid"] == hint for c in candidates):
        chosen = hint
        needs_pick = False

    if not chosen:
        out["status"] = "ambiguous" if needs_pick else "not_found"
        return out

    # Attach only if still unset, so two lanes racing cannot fight over it.
    db = SessionLocal()
    try:
        rel = db.get(Release, release_id)
        if rel and not rel.musicbrainz_id:
            rel.musicbrainz_id = chosen
            db.commit()
    finally:
        db.close()

    await coverart.ensure_cover_art([chosen])

    # Read `has_art` back off the CACHE, never off ensure_cover_art's return.
    # It reports only what it CHECKED this call and skips MBIDs already cached,
    # so a group another lane resolved earlier comes back absent -- which read as
    # "no art" and made two releases that do have covers report art=False.
    db = SessionLocal()
    try:
        cached = db.execute(text(
            "SELECT has_art FROM mb_cover_art WHERE musicbrainz_id = :m"
        ), {"m": chosen}).scalar()
    finally:
        db.close()
    out.update(status="attached", musicbrainz_id=chosen,
               has_art=None if cached is None else bool(cached))
    logger.info("Release %s '%s' -> release-group %s (art=%s)",
                release_id, title, chosen, out["has_art"])
    return out


def pending_release_ids(limit: int | None = None, readings_first: bool = True) -> list[int]:
    """Releases with no MBID yet, readings first.

    A release carrying a reading is a page someone is meant to look at, so it is
    resolved before the long tail of metadata-only rows.
    """
    db = SessionLocal()
    try:
        order = ("ORDER BY (charge_summary IS NULL), id" if readings_first else "ORDER BY id")
        sql = f"SELECT id FROM releases WHERE musicbrainz_id IS NULL {order}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [r[0] for r in db.execute(text(sql)).fetchall()]
    finally:
        db.close()


async def resolve_pending(limit: int = 25) -> dict:
    """Resolve a bounded batch. Bounded on purpose: MusicBrainz is 1 req/sec and
    503s freely under load, so this takes a slice per run rather than trying to
    drain the backlog in one pass.
    """
    stats = {"checked": 0, "attached": 0, "ambiguous": 0, "not_found": 0, "with_art": 0}
    for rid in pending_release_ids(limit=limit):
        try:
            res = await resolve_release_mbid(rid)
        except Exception:
            # Fail-soft per release: one bad row must not end the batch.
            logger.exception("Release MBID resolve failed for %s", rid)
            continue
        stats["checked"] += 1
        if res["status"] == "attached":
            stats["attached"] += 1
            if res.get("has_art"):
                stats["with_art"] += 1
        elif res["status"] in ("ambiguous", "not_found"):
            stats[res["status"]] += 1
    return stats
