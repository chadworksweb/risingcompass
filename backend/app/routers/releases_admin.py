"""Releases admin API -- the read + management surface for releases and their
tracklists.

Why this exists: until now `releases` had NO admin router at all. The only
release-touching endpoint was `POST /api/admin/artists/{slug}/rebuild-releases`,
which purges and re-resolves MusicBrainz-derived releases wholesale. There was no
way to see a release, fix its metadata, attach already-calibrated songs to it, or
correct a running order. Doing any of that meant a hand-written script against the
models (done twice on 2026-08-12).

Scope boundary: this router owns release IDENTITY and MEMBERSHIP -- title, type,
dates, which songs are on it, and in what order. It does NOT own the album
READING. The album charge/tier/prose/deadpan/topics belong to the album
calibrator, which is being rebuilt as the `rc-album` LEC lens (see
RISING-COMPASS-ALBUM-V3.md). Aggregates recomputed here are the same placeholder
mean `compute_release_charge` has always produced, and they stay that way until
the v3 album instrument lands.

**Running order is not cosmetic.** Under the v3 album model the song rows in
running order ARE the text the album lens reads, so a reorder invalidates any
album reading that was written against the old sequence. Reorder responses carry
`reading_invalidated` so the UI can say so.

Cookie auth (site-admin session), same posture as song_merge_admin.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Artist, Release, ReleaseSong, Song
from app.routers.admin import verify_admin_key
from app.services.artist_utils import compute_release_charge

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/releases",
    tags=["releases-admin"],
    dependencies=[Depends(verify_admin_key)],
)

VALID_TYPES = {"album", "ep", "single"}

# A release the album calibrator produced a real reading for. Everything else is
# metadata-only (MusicBrainz-derived, or created here and awaiting a reading).
READING_FIELDS = ("charge_summary", "arc_prose", "listener_effects_prose",
                  "societal_effects_prose", "deadpan_line")


def _has_reading(rel: Release) -> bool:
    return any(getattr(rel, f, None) for f in READING_FIELDS)


def _recompute_aggregates(db: Session, rel: Release) -> None:
    """Refresh track/calibrated/contamination counts and the placeholder charge.

    The charge here is the legacy mean. It is a PLACEHOLDER, not an album reading:
    the v3 album instrument composes a charge from center + vernier under the
    governing axis and will overwrite it. Kept so a freshly assembled release is
    not left with a NULL tier in listings.
    """
    rows = (
        db.query(ReleaseSong, Song)
        .join(Song, Song.id == ReleaseSong.song_id)
        .filter(ReleaseSong.release_id == rel.id)
        .all()
    )
    songs = [s for _, s in rows]
    rel.track_count = len(rows)
    scored = [s.charge_value for s in songs if s.charge_value is not None]
    rel.calibrated_count = len(scored)
    rel.contamination_count = sum(1 for s in songs if s.contaminated)
    if scored and not _has_reading(rel):
        avg, color, _label, _hex = compute_release_charge(scored)
        rel.charge_value = avg
        rel.rubric_color = color


def _release_row(rel: Release, artist_name: str | None = None) -> dict:
    return {
        "id": rel.id,
        "artist_id": rel.artist_id,
        "artist": artist_name,
        "title": rel.title,
        "release_type": rel.release_type,
        "release_date": rel.release_date.isoformat() if rel.release_date else None,
        "release_year": rel.release_year,
        "rubric_color": rel.rubric_color,
        "charge_value": rel.charge_value,
        "track_count": rel.track_count,
        "calibrated_count": rel.calibrated_count,
        "contamination_count": rel.contamination_count,
        "source": rel.source,
        "musicbrainz_id": rel.musicbrainz_id,
        "has_reading": _has_reading(rel),
    }


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------

@router.get("")
def list_releases(
    q: Optional[str] = Query(None, description="title or artist substring"),
    release_type: Optional[str] = Query(None),
    source: Optional[str] = Query(None, description="'manual' matches source IS NULL"),
    has_reading: Optional[bool] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Release, Artist.name).join(Artist, Artist.id == Release.artist_id)
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.filter(or_(func.lower(Release.title).like(like),
                                 func.lower(Artist.name).like(like)))
    if release_type:
        query = query.filter(Release.release_type == release_type)
    if source == "manual":
        query = query.filter(Release.source.is_(None))
    elif source:
        query = query.filter(Release.source == source)

    total = query.count()
    rows = (query.order_by(Release.release_year.desc().nullslast(), Release.title)
            .offset(offset).limit(limit).all())
    out = [_release_row(rel, name) for rel, name in rows]
    if has_reading is not None:
        out = [r for r in out if r["has_reading"] is has_reading]
    return {"total": total, "releases": out}


@router.get("/stats")
def release_stats(db: Session = Depends(get_db)):
    total = db.query(func.count(Release.id)).scalar() or 0
    by_type = dict(
        db.query(Release.release_type, func.count(Release.id))
        .group_by(Release.release_type).all()
    )
    mb = db.query(func.count(Release.id)).filter(
        Release.musicbrainz_id.isnot(None)).scalar() or 0
    with_reading = db.query(func.count(Release.id)).filter(
        Release.charge_summary.isnot(None)).scalar() or 0
    return {
        "total": total,
        "by_type": by_type,
        "musicbrainz_derived": mb,
        "with_reading": with_reading,
        "awaiting_reading": total - with_reading,
    }


@router.get("/{release_id}")
def get_release(release_id: int, db: Session = Depends(get_db)):
    rel = db.get(Release, release_id)
    if not rel:
        raise HTTPException(404, "Release not found")
    artist = db.get(Artist, rel.artist_id)
    rows = (
        db.query(ReleaseSong, Song)
        .outerjoin(Song, Song.id == ReleaseSong.song_id)
        .filter(ReleaseSong.release_id == release_id)
        .order_by(ReleaseSong.track_number.nullslast(), ReleaseSong.id)
        .all()
    )
    tracks = [{
        "release_song_id": rs.id,
        "track_number": rs.track_number,
        "song_id": rs.song_id,
        "title": s.title if s else None,
        "artist": s.artist if s else None,
        "rubric_color": s.rubric_color if s else None,
        "charge_value": s.charge_value if s else None,
        "contaminated": bool(s.contaminated) if s else False,
        "dogma_referenced": bool(s.dogma_referenced) if s else False,
    } for rs, s in rows]

    out = _release_row(rel, artist.name if artist else None)
    out["artist_slug"] = artist.slug if artist else None
    out["tracks"] = tracks
    out["reading"] = {
        "charge_summary": rel.charge_summary,
        "arc_prose": rel.arc_prose,
        "listener_effects_prose": rel.listener_effects_prose,
        "societal_effects_prose": rel.societal_effects_prose,
        "deadpan_line": rel.deadpan_line,
        "topics": json.loads(rel.topics) if rel.topics else None,
        "topic_audit": json.loads(rel.topic_audit) if rel.topic_audit else None,
    }
    return out


@router.get("/{release_id}/candidate-songs")
def candidate_songs(release_id: int, db: Session = Depends(get_db)):
    """Calibrated songs credited to this release's artist that are not on it yet.

    The picker for assembling a release from already-approved song rows, which is
    the operation the v3 album model assumes: tracks first, album second.
    """
    rel = db.get(Release, release_id)
    if not rel:
        raise HTTPException(404, "Release not found")
    on_it = {r[0] for r in db.query(ReleaseSong.song_id)
             .filter(ReleaseSong.release_id == release_id).all()}
    from app.models import SongArtist
    rows = (
        db.query(Song)
        .join(SongArtist, SongArtist.song_id == Song.id)
        .filter(SongArtist.artist_id == rel.artist_id)
        .filter(Song.rubric_color.isnot(None))
        .order_by(Song.title)
        .all()
    )
    return {"songs": [{
        "song_id": s.id, "title": s.title, "artist": s.artist,
        "rubric_color": s.rubric_color, "charge_value": s.charge_value,
        "contaminated": bool(s.contaminated),
        "already_on_release": s.id in on_it,
    } for s in rows]}


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------

class CreateReleaseRequest(BaseModel):
    artist_slug: str
    title: str = Field(..., min_length=1, max_length=300)
    release_type: str = "album"
    release_date: Optional[str] = None   # ISO yyyy-mm-dd
    release_year: Optional[int] = None
    song_ids: list[int] = Field(default_factory=list, description="in running order")


@router.post("")
def create_release(req: CreateReleaseRequest, db: Session = Depends(get_db)):
    if req.release_type not in VALID_TYPES:
        raise HTTPException(400, f"release_type must be one of {sorted(VALID_TYPES)}")
    artist = db.query(Artist).filter(Artist.slug == req.artist_slug).first()
    if not artist:
        raise HTTPException(404, f"Artist '{req.artist_slug}' not found")

    title = req.title.strip()
    dupe = db.query(Release).filter(
        Release.artist_id == artist.id, func.lower(Release.title) == title.lower()).first()
    if dupe:
        raise HTTPException(409, f"'{title}' already exists for this artist (id={dupe.id})")

    rdate = None
    if req.release_date:
        try:
            rdate = date.fromisoformat(req.release_date)
        except ValueError:
            raise HTTPException(400, "release_date must be ISO yyyy-mm-dd")
    ryear = req.release_year or (rdate.year if rdate else None)

    rel = Release(
        artist_id=artist.id, title=title, release_type=req.release_type,
        release_date=rdate, release_year=ryear, source=None,
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(rel)
    db.flush()

    seen: set[int] = set()
    for i, sid in enumerate(req.song_ids, start=1):
        if sid in seen:
            raise HTTPException(400, f"song {sid} listed twice")
        if not db.get(Song, sid):
            raise HTTPException(404, f"song {sid} not found")
        seen.add(sid)
        db.add(ReleaseSong(release_id=rel.id, song_id=sid, track_number=i))
    db.flush()
    _recompute_aggregates(db, rel)
    db.commit()
    db.refresh(rel)
    logger.info("releases_admin: created release %s (%s) with %d tracks",
                rel.id, title, len(seen))
    return _release_row(rel, artist.name)


class PatchReleaseRequest(BaseModel):
    title: Optional[str] = None
    release_type: Optional[str] = None
    release_date: Optional[str] = None
    release_year: Optional[int] = None


@router.patch("/{release_id}")
def patch_release(release_id: int, req: PatchReleaseRequest, db: Session = Depends(get_db)):
    rel = db.get(Release, release_id)
    if not rel:
        raise HTTPException(404, "Release not found")
    if req.title is not None:
        rel.title = req.title.strip()
    if req.release_type is not None:
        if req.release_type not in VALID_TYPES:
            raise HTTPException(400, f"release_type must be one of {sorted(VALID_TYPES)}")
        rel.release_type = req.release_type
    if req.release_date is not None:
        if req.release_date == "":
            rel.release_date = None
        else:
            try:
                rel.release_date = date.fromisoformat(req.release_date)
            except ValueError:
                raise HTTPException(400, "release_date must be ISO yyyy-mm-dd")
            if req.release_year is None:
                rel.release_year = rel.release_date.year
    if req.release_year is not None:
        rel.release_year = req.release_year
    db.commit()
    db.refresh(rel)
    artist = db.get(Artist, rel.artist_id)
    return _release_row(rel, artist.name if artist else None)


class SetTracksRequest(BaseModel):
    song_ids: list[int] = Field(..., description="the full tracklist, in running order")


@router.put("/{release_id}/tracks")
def set_tracks(release_id: int, req: SetTracksRequest, db: Session = Depends(get_db)):
    """Replace the tracklist with the supplied songs, in the supplied order.

    Used for both membership changes and pure reorders. The response flags
    `reading_invalidated` whenever the release already carries an album reading,
    because `arc_prose` and any closing-stance judgment were written against the
    previous sequence. Under the v3 album model a reorder is a re-read, not a
    metadata edit.
    """
    rel = db.get(Release, release_id)
    if not rel:
        raise HTTPException(404, "Release not found")

    prior = [r[0] for r in db.query(ReleaseSong.song_id)
             .filter(ReleaseSong.release_id == release_id)
             .order_by(ReleaseSong.track_number.nullslast(), ReleaseSong.id).all()]

    seen: set[int] = set()
    for sid in req.song_ids:
        if sid in seen:
            raise HTTPException(400, f"song {sid} listed twice")
        if not db.get(Song, sid):
            raise HTTPException(404, f"song {sid} not found")
        seen.add(sid)

    db.query(ReleaseSong).filter(ReleaseSong.release_id == release_id).delete()
    db.flush()
    for i, sid in enumerate(req.song_ids, start=1):
        db.add(ReleaseSong(release_id=release_id, song_id=sid, track_number=i))
    db.flush()
    _recompute_aggregates(db, rel)
    db.commit()

    changed = prior != list(req.song_ids)
    return {
        "release_id": release_id,
        "track_count": len(req.song_ids),
        "order_changed": changed,
        "reading_invalidated": bool(changed and _has_reading(rel)),
        "note": ("The album reading was written against the previous running order. "
                 "arc_prose and the closing-stance read need redoing."
                 if changed and _has_reading(rel) else None),
    }


@router.delete("/{release_id}")
def delete_release(release_id: int, db: Session = Depends(get_db)):
    rel = db.get(Release, release_id)
    if not rel:
        raise HTTPException(404, "Release not found")
    if rel.musicbrainz_id:
        raise HTTPException(
            400,
            "This release is MusicBrainz-derived. Use the artist's rebuild-releases "
            "action instead, so it is not simply re-created on the next resolve.",
        )
    title = rel.title
    db.query(ReleaseSong).filter(ReleaseSong.release_id == release_id).delete()
    db.delete(rel)
    db.commit()
    logger.info("releases_admin: deleted release %s (%s)", release_id, title)
    return {"deleted": release_id, "title": title}
