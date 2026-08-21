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
from app.models import (
    Artist, CalibrationRun, Release, ReleaseProseVersion, ReleaseSong, Song,
)
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


def _json_col(raw):
    """Decode one of the JSON-encoded Text columns, fail-soft.

    A malformed value costs the panel that one block, never the whole release.
    """
    try:
        return json.loads(raw) if raw else None
    except (ValueError, TypeError):
        return None


def _iso(dt):
    return dt.isoformat() if dt else None


def _has_reading(rel: Release) -> bool:
    return any(getattr(rel, f, None) for f in READING_FIELDS)


# Provenance values that mean "assembled by hand for a lens read". A release
# carrying one of these is waiting for its reading, so the placeholder mean must
# stay off it. `prepare_release.py` stamps `manual_terminal`; `album_backfill`
# is the older hand-built lane.
_HAND_BUILT_SOURCES = ("manual_terminal", "album_backfill")


def _pending_read(db: Session, rel: Release) -> bool:
    """True when this release was assembled FOR a lens read that has not landed.

    `prepare_release.py` deliberately creates a release with `rubric_color` and
    `charge_value` NULL, because the aggregate of the track charges must not
    exist anywhere the reader might see it before the read: a mean sitting in
    the row is a conclusion the read has not earned, and seeing one anchors the
    read on it.

    Without this test, touching such a release through this admin stamps the
    placeholder mean onto it and quietly destroys that property. `_has_reading`
    cannot cover it, since it asks whether a reading EXISTS, not whether one is
    coming.

    TEST ON THE STAMP, NOT ON WHAT IS ABSENT. The first version of this asked
    whether the release had neither a musicbrainz_id nor a spotify_id, on the
    reasoning that a hand-built release arrives with neither. True at creation
    and false soon after: the nightly cover-art sweep exists to attach MBIDs to
    releases that lack them, so that test decays into False on exactly the rows
    it was written to protect, and does it silently.
    """
    return rel.source in _HAND_BUILT_SOURCES


def _recompute_aggregates(db: Session, rel: Release) -> None:
    """Refresh track/calibrated/contamination counts and the placeholder charge.

    The charge here is the legacy mean. It is a PLACEHOLDER for catalogue
    releases nobody will read with the album lens, kept so a freshly assembled
    release is not left with a NULL tier in listings. It is never written over a
    release that carries a reading, and never onto one awaiting one.
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
    if scored and not _has_reading(rel) and not _pending_read(db, rel):
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
        "topics": _json_col(rel.topics),
        "topic_audit": _json_col(rel.topic_audit),
        # The v3 half. This panel used to stop at the seven fields above, which
        # were all the Album Charger ever produced -- so an admin looking at a
        # lens reading could not see its findings, its confidence, or the label
        # it wrote, and had no way to tell a v3 read from a pre-v3 backfill.
        "contaminated": bool(rel.contaminated),
        "contamination_note": rel.contamination_note,
        "dogma_referenced": bool(rel.dogma_referenced),
        "dogma_note": rel.dogma_note,
        "confidence": rel.confidence,
        "psyche_facts": _json_col(rel.psyche_facts),
        "effects_pl": _json_col(rel.effects_pl),
        "calibration_failed": bool(rel.calibration_failed),
        "societal_prose_generated_at": _iso(rel.societal_prose_generated_at),
        "societal_prose_model": rel.societal_prose_model,
        # The archive slots, so "what did the last write replace" is answerable
        # from the same screen rather than from a query.
        "prior_arc_prose": rel.prior_arc_prose,
        "prior_listener_effects_prose": rel.prior_listener_effects_prose,
        "prior_societal_effects_prose": rel.prior_societal_effects_prose,
    }
    out["runs"] = _release_runs(db, release_id)
    out["prose_versions"] = _release_prose_versions(db, release_id)
    # Which instrument produced what is on the row. A release carrying a reading
    # with no run behind it is a pre-v3 backfill, and that is worth stating
    # rather than leaving an admin to infer it from an empty run list.
    out["reading_provenance"] = (
        None if not _has_reading(rel)
        else ("lens" if out["runs"] else "pre_v3")
    )
    return out


def _release_runs(db: Session, release_id: int) -> list[dict]:
    """The album run ledger for this release, newest first.

    Keyed on `calibration_runs.release_id` (migration 149). `coherence` is the
    album lane's structural axis and rides the column `route` uses on a song run,
    so a song run and an album run share this table without sharing a shape.
    """
    rows = (
        db.query(CalibrationRun)
        .filter(CalibrationRun.release_id == release_id)
        .order_by(CalibrationRun.run_at.desc(), CalibrationRun.id.desc())
        .all()
    )
    return [{
        "id": r.id,
        "run_at": _iso(r.run_at),
        "superseded": bool(r.superseded),
        "superseded_reason": r.superseded_reason,
        "rubric_color": r.rubric_color,
        "charge_value": r.charge_value,
        "coherence": r.coherence,
        "visceral_charge": r.visceral_charge,
        "harm_value": r.harm_value,
        "harm_pervasive": bool(r.harm_pervasive),
        "transcendence_value": r.transcendence_value,
        "governing_axis": r.governing_axis,
        "center": r.center,
        "vernier": _json_col(r.vernier),
        "gut_divergence": r.gut_divergence,
        "confidence": r.confidence,
        "triggered_by": r.triggered_by,
        "agent_model": r.agent_model,
        "calibration_failed": bool(r.calibration_failed),
        # The stored argument. This is the whole reason the ledger takes a
        # release: it is the only place the album's reasoning survives.
        "reasoning": r.reasoning,
    } for r in rows]


def _release_prose_versions(db: Session, release_id: int) -> list[dict]:
    """Archived prose for this release, newest first.

    `release_prose_versions.release_id` is deliberately NOT an FK -- a catalogue
    rebuild churns `releases.id` -- so this is a plain equality match and can
    legitimately return rows for a release that was rebuilt underneath it.
    """
    rows = (
        db.query(ReleaseProseVersion)
        .filter(ReleaseProseVersion.release_id == release_id)
        .order_by(ReleaseProseVersion.written_at.desc(), ReleaseProseVersion.id.desc())
        .all()
    )
    return [{
        "id": v.id,
        "written_at": _iso(v.written_at),
        "lane": v.lane,
        "prose": v.prose,
        "model": v.model,
        "trigger": v.trigger,
        "rubric_color": v.rubric_color,
        "charge_value": v.charge_value,
    } for v in rows]


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
