"""Reader-filed "this cover is wrong" reports, and the admin queue that resolves them.

THE GAP THIS FILLS. Two automated checks stand between a song and a wrong cover:
the artist-credit check in `musicbrainz._pick_release_group` (wrong ARTIST) and
`scripts/audit_song_cover_art.py` (a release issued years after the song charted).
Neither can catch a right artist on a contemporaneous but wrong release -- a 1990
single wearing another 1990 record's sleeve -- and no amount of stored metadata
can, because the two are identical in the data. A person looking at the page tells
them apart instantly. This is how what they saw gets back.

NO SIGN-IN. Wrong art is a factual claim anyone can see, and a Clerk gate would
drop nearly all of the signal to buy an attribution nobody needs. The report is
safe to leave open because it changes NOTHING on its own: every one is resolved by
an admin. Abuse controls are the honeypot, the per-IP rate limit, and one report
per device per song per pick.

WHAT A CONFIRM DOES. It clears `songs.release_group_mbid` + `release_group_date`
and deliberately LEAVES `release_group_checked_at` set -- the codified "recorded
miss", so the backfill skips the song and the wrong art stays gone instead of
being re-picked on the next pass. The confirmed row is also the durable rejection:
`scripts/backfill_song_cover_art.py` excludes it from a `--recheck-misses`
re-resolve, so a deliberate retry can find better art but never lands back on the
picture a reader already rejected.

ART INHERITED FROM A RELEASE IS NOT FIXED HERE. A song with a Release link shows
its release's cover (see `songs._enrich_with_release_context`), which the release
page and every sibling track share. Clearing the song column would do nothing
visible. Those reports are still recorded and confirmed, and the response says
plainly that the fix belongs in Artists & Releases -- silently succeeding while
changing nothing is the one outcome worth ruling out.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (
    Release, ReleaseSong, Song, SongCoverArtReport, SongSlug,
)
from app.routers.admin import verify_admin_key
from app.routers.analyzer import limiter
from app.services import coverart

logger = logging.getLogger(__name__)

# Mounted BARE in main.py, like subscribe.router -- the song page posts this with
# no X-Api-Key and no session.
router = APIRouter(tags=["cover-art-reports"])
admin_router = APIRouter(prefix="/api/admin/cover-art-reports", tags=["cover-art-admin"])

NOTE_MAX = 500
VALID_ACTIONS = {"confirm", "dismiss"}


class CoverArtReportIn(BaseModel):
    # Optional, and deliberately so: the click IS the report. A required
    # explanation would cost more reports than the extra detail is worth.
    note: str = Field(default="", max_length=NOTE_MAX)
    device_id: str = ""
    hp_website: str = ""


def _resolve_song(db: Session, slug: str) -> Song:
    row = (
        db.query(Song)
        .join(SongSlug, SongSlug.song_id == Song.id)
        .filter(SongSlug.slug == slug)
        .first()
    )
    if row is None:
        raise HTTPException(404, "Song not found")
    return row


def _displayed_art(db: Session, song: Song) -> tuple[Optional[str], Optional[str]]:
    """The release-group MBID actually serving this song's art, and where it came
    from ('release' | 'song').

    Mirrors `songs._enrich_with_release_context`'s preference order exactly. If the
    two ever drift, a report names a picture the reader was not looking at, which
    is the whole thing this field exists to prevent.
    """
    release_mbid = None
    link = db.query(ReleaseSong).filter(ReleaseSong.song_id == song.id).first()
    if link:
        release = db.query(Release).get(link.release_id)
        if release:
            release_mbid = release.musicbrainz_id

    winner = coverart.mbid_with_art(db, [release_mbid, song.release_group_mbid])
    if not winner:
        return None, None
    return winner, ("release" if winner == release_mbid else "song")


@router.post("/api/songs/{slug}/cover-art-report", status_code=201)
@limiter.limit("10/hour")
def report_cover_art(
    slug: str,
    data: CoverArtReportIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """File a "wrong cover art" report against a song page. No account needed."""
    if data.hp_website.strip():
        # Honeypot tripped. Generic 422 with no hint about which field gave it
        # away, matching analyzer._check_bot_protection. Turnstile is deliberately
        # NOT required here: the song page carries no widget, and a payload this
        # small with a per-IP cap is not worth a challenge on every song page.
        raise HTTPException(422, "Submission rejected.")

    song = _resolve_song(db, slug)
    mbid, source = _displayed_art(db, song)
    if not mbid:
        # Nothing is being shown, so there is nothing to be wrong about. Told
        # plainly rather than filed, so the queue never fills with reports about
        # art that had already been pulled by the time they were sent.
        raise HTTPException(409, "This song is not showing any cover art.")

    report = SongCoverArtReport(
        song_id=song.id,
        reported_mbid=mbid,
        mbid_source=source,
        note=(data.note or "").strip()[:NOTE_MAX] or None,
        device_id=(data.device_id or "").strip() or None,
        ip_address=request.client.host if request.client else None,
        status="open",
        environment=settings.environment,
    )
    db.add(report)
    try:
        db.commit()
    except IntegrityError:
        # Same device, same song, same picture. A second click is the same claim,
        # so it reads as success -- a repeat reporter should never be shown a
        # failure for agreeing with themselves.
        db.rollback()
        return {"ok": True, "duplicate": True}

    return {"ok": True, "duplicate": False}


# --- Admin ---

class ResolveIn(BaseModel):
    action: str                      # confirm | dismiss
    note: str = Field(default="", max_length=NOTE_MAX)


@admin_router.get("", dependencies=[Depends(verify_admin_key)])
def list_reports(
    status: str = "open",
    environment: str = "prod",
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """The queue, grouped by the PICK rather than by the report.

    Ten people reporting one wrong cover is one thing to fix, and the count is
    itself the signal -- a grouped row says how many independent readers saw it.
    """
    q = (
        db.query(
            func.min(SongCoverArtReport.id).label("id"),
            SongCoverArtReport.song_id,
            SongCoverArtReport.reported_mbid,
            SongCoverArtReport.mbid_source,
            func.count(SongCoverArtReport.id).label("report_count"),
            func.max(SongCoverArtReport.created_at).label("latest_at"),
        )
        .filter(SongCoverArtReport.environment == environment)
        .group_by(
            SongCoverArtReport.song_id,
            SongCoverArtReport.reported_mbid,
            SongCoverArtReport.mbid_source,
        )
        .order_by(func.count(SongCoverArtReport.id).desc(),
                  func.max(SongCoverArtReport.created_at).desc())
    )
    if status:
        q = q.filter(SongCoverArtReport.status == status)
    groups = q.limit(limit).all()
    if not groups:
        return {"items": [], "environment": environment, "status": status}

    song_ids = {g.song_id for g in groups}
    songs = {s.id: s for s in db.query(Song).filter(Song.id.in_(song_ids)).all()}
    slugs = {
        r.song_id: r.slug
        for r in db.query(SongSlug).filter(SongSlug.song_id.in_(song_ids)).all()
    }
    # The notes carry the only thing the count can't: what the reader thinks the
    # cover actually is.
    notes_q = (
        db.query(SongCoverArtReport)
        .filter(SongCoverArtReport.song_id.in_(song_ids))
        .filter(SongCoverArtReport.environment == environment)
        .filter(SongCoverArtReport.note.isnot(None))
    )
    if status:
        notes_q = notes_q.filter(SongCoverArtReport.status == status)
    notes_by_group: dict[tuple, list[str]] = {}
    for row in notes_q.all():
        notes_by_group.setdefault((row.song_id, row.reported_mbid), []).append(row.note)

    items = []
    for g in groups:
        song = songs.get(g.song_id)
        items.append({
            "id": g.id,
            "song_id": g.song_id,
            "song_title": song.title if song else None,
            "song_artist": song.artist if song else None,
            "song_slug": slugs.get(g.song_id),
            "reported_mbid": g.reported_mbid,
            "mbid_source": g.mbid_source,
            "still_current": bool(song and song.release_group_mbid == g.reported_mbid),
            "release_group_date": song.release_group_date if song else None,
            "cover_url": coverart.coverart_urls(g.reported_mbid)["thumb_url"]
                         if g.reported_mbid else None,
            "report_count": g.report_count,
            "latest_at": g.latest_at,
            "notes": notes_by_group.get((g.song_id, g.reported_mbid), [])[:5],
        })
    return {"items": items, "environment": environment, "status": status}


@admin_router.get("/stats", dependencies=[Depends(verify_admin_key)])
def report_stats(environment: str = "prod", db: Session = Depends(get_db)):
    rows = (
        db.query(SongCoverArtReport.status, func.count(SongCoverArtReport.id))
        .filter(SongCoverArtReport.environment == environment)
        .group_by(SongCoverArtReport.status)
        .all()
    )
    counts = {status: n for status, n in rows}
    return {
        "environment": environment,
        "open": counts.get("open", 0),
        "confirmed": counts.get("confirmed", 0),
        "dismissed": counts.get("dismissed", 0),
    }


@admin_router.post("/{report_id}/resolve", dependencies=[Depends(verify_admin_key)])
def resolve_report(
    report_id: int,
    data: ResolveIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """Resolve every open report about the same pick, and pull the art on confirm.

    Resolution cascades across the group because the claim is about the picture,
    not about the person who sent it -- leaving nine siblings open after acting on
    the tenth would just re-present work already done.
    """
    if data.action not in VALID_ACTIONS:
        raise HTTPException(400, f"Invalid action. One of: {', '.join(sorted(VALID_ACTIONS))}")

    report = db.query(SongCoverArtReport).filter(SongCoverArtReport.id == report_id).first()
    if report is None:
        raise HTTPException(404, "Report not found")

    song = db.query(Song).filter(Song.id == report.song_id).first()
    new_status = "confirmed" if data.action == "confirm" else "dismissed"
    admin = getattr(request.state, "admin_username", None)

    cleared = False
    needs_release_action = False
    if data.action == "confirm" and song is not None:
        if report.mbid_source == "release":
            # The song wears its release's cover; clearing the song column would
            # change nothing on the page. Say so instead of reporting a fix.
            needs_release_action = True
        elif song.release_group_mbid == report.reported_mbid:
            song.release_group_mbid = None
            song.release_group_date = None
            # release_group_checked_at is left ALONE on purpose: a stamped
            # checked_at with a NULL mbid is the recorded miss, which is what
            # keeps the backfill from re-resolving straight back to this pick.
            cleared = True
        # else: already re-resolved to something else since the report was filed,
        # so there is nothing to clear -- but the rejection still gets recorded.

    now = datetime.now(timezone.utc)
    affected = (
        db.query(SongCoverArtReport)
        .filter(SongCoverArtReport.song_id == report.song_id)
        .filter(SongCoverArtReport.reported_mbid == report.reported_mbid)
        .filter(SongCoverArtReport.status == "open")
        .update(
            {
                "status": new_status,
                "resolved_at": now,
                "resolved_by": admin,
                "resolution_note": (data.note or "").strip()[:NOTE_MAX] or None,
            },
            synchronize_session=False,
        )
    )
    db.commit()

    return {
        "ok": True,
        "status": new_status,
        "resolved_count": affected,
        "art_cleared": cleared,
        "needs_release_action": needs_release_action,
        "detail": (
            "This song inherits its cover from a linked release, so the fix "
            "belongs in Artists & Releases. Nothing on the song row was changed."
            if needs_release_action else None
        ),
    }
