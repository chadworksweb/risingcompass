"""Admin endpoints for Lyrical Charger submitted_songs — viewer, stats, promotion."""

from datetime import datetime, date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SubmittedSong, LibrarySong
from app.schemas import SubmittedSongOut, SubmissionStatsOut
from app.routers.admin import verify_admin_key

router = APIRouter(prefix="/api/admin/submissions", tags=["submissions-admin"])


@router.get("", response_model=list[SubmittedSongOut], dependencies=[Depends(verify_admin_key)])
def list_submissions(
    color: str | None = Query(None),
    source: str | None = Query(None),
    contaminated: bool | None = Query(None),
    since: date | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List submitted songs with optional filters."""
    q = db.query(SubmittedSong)
    if color:
        q = q.filter(SubmittedSong.rubric_color == color)
    if source:
        q = q.filter(SubmittedSong.source == source)
    if contaminated is not None:
        q = q.filter(SubmittedSong.contaminated == contaminated)
    if since:
        q = q.filter(SubmittedSong.submitted_at >= datetime.combine(since, datetime.min.time()))
    return q.order_by(SubmittedSong.submitted_at.desc()).offset(offset).limit(limit).all()


@router.get("/stats", response_model=SubmissionStatsOut, dependencies=[Depends(verify_admin_key)])
def submission_stats(db: Session = Depends(get_db)):
    """Aggregate stats for submitted songs."""
    total = db.query(func.count(SubmittedSong.id)).scalar() or 0

    today_start = datetime.combine(date.today(), datetime.min.time())
    today_count = (
        db.query(func.count(SubmittedSong.id))
        .filter(SubmittedSong.submitted_at >= today_start)
        .scalar() or 0
    )

    color_rows = (
        db.query(SubmittedSong.rubric_color, func.count(SubmittedSong.id))
        .group_by(SubmittedSong.rubric_color)
        .all()
    )
    by_color = {row[0]: row[1] for row in color_rows}

    source_rows = (
        db.query(SubmittedSong.source, func.count(SubmittedSong.id))
        .group_by(SubmittedSong.source)
        .all()
    )
    by_source = {row[0]: row[1] for row in source_rows}

    contam = (
        db.query(func.count(SubmittedSong.id))
        .filter(SubmittedSong.contaminated == True)
        .scalar() or 0
    )

    avg_conf = db.query(func.avg(SubmittedSong.confidence)).scalar()

    return SubmissionStatsOut(
        total=total,
        today=today_count,
        by_color=by_color,
        by_source=by_source,
        contaminated_count=contam,
        avg_confidence=round(avg_conf, 3) if avg_conf is not None else None,
    )


@router.delete("/{submission_id}", dependencies=[Depends(verify_admin_key)])
def delete_submission(submission_id: int, db: Session = Depends(get_db)):
    """Delete a submitted song."""
    sub = db.query(SubmittedSong).filter(SubmittedSong.id == submission_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail=f"Submission ID {submission_id} not found")
    title, artist = sub.title, sub.artist
    db.delete(sub)
    db.commit()
    return {"deleted": submission_id, "title": title, "artist": artist}


@router.post("/{submission_id}/promote", dependencies=[Depends(verify_admin_key)])
def promote_submission(submission_id: int, db: Session = Depends(get_db)):
    """Promote a submitted song into library_songs. Does NOT delete the submission."""
    sub = db.query(SubmittedSong).filter(SubmittedSong.id == submission_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail=f"Submission ID {submission_id} not found")

    if not sub.title or not sub.artist:
        raise HTTPException(status_code=422, detail="Cannot promote: title and artist are required")

    # Check for duplicate in library
    existing = (
        db.query(LibrarySong)
        .filter(LibrarySong.title.ilike(sub.title), LibrarySong.artist.ilike(sub.artist))
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Already in library as ID {existing.id}: {existing.title} — {existing.artist}",
        )

    library_song = LibrarySong(
        title=sub.title,
        artist=sub.artist,
        rubric_color=sub.rubric_color,
        charge_value=sub.charge_value,
        contaminated=sub.contaminated,
        contamination_note=sub.contamination_note,
        charge_summary=sub.charge_summary,
        source="crowd",
    )
    db.add(library_song)
    db.commit()
    db.refresh(library_song)

    return {
        "promoted": True,
        "submission_id": submission_id,
        "library_song_id": library_song.id,
        "title": library_song.title,
        "artist": library_song.artist,
    }
