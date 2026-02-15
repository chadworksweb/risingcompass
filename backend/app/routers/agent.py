"""Admin endpoints for the Compass Agent — trigger, list, view, approve, reject, edit drafts."""

import math
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AgentDraft, AgentDraftSong, DailyReading, ReadingSong, Song
from app.schemas import (
    DraftOut, DraftTriggerIn, DraftUpdate,
    PaginatedDrafts, DraftSummary, SongFeedIn, SongOut,
)
from app.routers.admin import verify_admin_key
from app.services.agents.compass_agent import run_compass_agent
from app.services.agents.chart_source import fetch_top_songs
from app.services.agents.email_notifier import send_draft_email
from app.services.compass_calc import compute_degree
from app.services.charge_calc import degree_to_charge
from app.services.contamination import count_contaminated

router = APIRouter(prefix="/api/admin/agent", tags=["agent"])


@router.post("/classify", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def trigger_classification(data: DraftTriggerIn, db: Session = Depends(get_db)):
    """Trigger classification on a list of songs (calibration mode).

    Creates an AgentDraft with Claude-generated classifications.
    """
    songs_input = [s.model_dump() for s in data.songs]
    reading_date = data.date or date.today()

    draft = run_compass_agent(songs_input, db, reading_date=reading_date)
    return draft


@router.post("/classify-live", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def classify_live(db: Session = Depends(get_db)):
    """Fetch today's Spotify Global Top 10 and classify them.

    No input needed — pulls live chart data and runs the full pipeline.
    """
    songs = fetch_top_songs(count=20)
    if not songs:
        raise HTTPException(status_code=502, detail="Failed to fetch chart data from Spotify")

    draft = run_compass_agent(songs, db, reading_date=date.today())
    return draft


@router.get("/drafts", response_model=PaginatedDrafts, dependencies=[Depends(verify_admin_key)])
def list_drafts(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List all agent drafts, newest first."""
    total = db.query(AgentDraft).count()
    pages = math.ceil(total / per_page) if total > 0 else 1
    drafts = (
        db.query(AgentDraft)
        .order_by(AgentDraft.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return PaginatedDrafts(
        items=[DraftSummary.model_validate(d) for d in drafts],
        total=total,
        page=page,
        pages=pages,
    )


@router.get("/drafts/{draft_id}", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def get_draft(draft_id: int, db: Session = Depends(get_db)):
    """Get a single draft with all songs."""
    draft = db.query(AgentDraft).filter(AgentDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@router.post("/drafts/{draft_id}/approve", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def approve_draft(draft_id: int, db: Session = Depends(get_db)):
    """Approve a draft and publish it as a DailyReading."""
    draft = db.query(AgentDraft).filter(AgentDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail=f"Draft is already {draft.status}")

    # Check for existing reading on this date
    existing = db.query(DailyReading).filter(DailyReading.date == draft.date).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A reading already exists for {draft.date}. Delete it first or reject this draft.",
        )

    # Create DailyReading from draft
    reading = DailyReading(
        date=draft.date,
        compass_degree=draft.compass_degree,
        charge_level=draft.charge_level,
        contamination_count=draft.contamination_count,
        editorial_summary=draft.editorial_summary,
    )
    db.add(reading)
    db.flush()

    for song in draft.songs:
        rs = ReadingSong(
            reading_id=reading.id,
            title=song.title,
            artist=song.artist,
            position=song.position,
            rubric_color=song.rubric_color,
            charge_value=song.charge_value,
            contaminated=song.contaminated,
            contamination_note=song.contamination_note,
            charge_summary=song.charge_summary,
            chart_source=song.chart_source,
        )
        db.add(rs)

    draft.status = "approved"
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/drafts/{draft_id}/reject", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def reject_draft(draft_id: int, db: Session = Depends(get_db)):
    """Mark a draft as rejected."""
    draft = db.query(AgentDraft).filter(AgentDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail=f"Draft is already {draft.status}")

    draft.status = "rejected"
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/drafts/{draft_id}/resend-email", dependencies=[Depends(verify_admin_key)])
def resend_draft_email(draft_id: int, db: Session = Depends(get_db)):
    """Resend the notification email for an existing draft."""
    from app.config import settings

    draft = db.query(AgentDraft).filter(AgentDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    sent = send_draft_email(draft, draft.songs, settings, db=db)
    if not sent:
        raise HTTPException(status_code=500, detail="Email failed — check SMTP config")
    return {"status": "sent", "to": settings.approval_email}


@router.put("/drafts/{draft_id}", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def update_draft(draft_id: int, data: DraftUpdate, db: Session = Depends(get_db)):
    """Edit a draft before approving — change colors, summaries, etc."""
    draft = db.query(AgentDraft).filter(AgentDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail=f"Cannot edit a {draft.status} draft")

    if data.editorial_summary is not None:
        draft.editorial_summary = data.editorial_summary

    if data.songs is not None:
        existing_songs = sorted(draft.songs, key=lambda s: s.position)
        if len(data.songs) != len(existing_songs):
            raise HTTPException(
                status_code=400,
                detail=f"Song count mismatch: draft has {len(existing_songs)}, update has {len(data.songs)}",
            )

        for existing, update in zip(existing_songs, data.songs):
            if update.rubric_color is not None:
                existing.rubric_color = update.rubric_color
            if update.charge_value is not None:
                existing.charge_value = update.charge_value
            if update.contaminated is not None:
                existing.contaminated = update.contaminated
            if update.contamination_note is not None:
                existing.contamination_note = update.contamination_note
            if update.charge_summary is not None:
                existing.charge_summary = update.charge_summary
            if update.message_analysis is not None:
                existing.message_analysis = update.message_analysis
            if update.expression_analysis is not None:
                existing.expression_analysis = update.expression_analysis
            if update.intention_analysis is not None:
                existing.intention_analysis = update.intention_analysis

        # Recalculate compass metrics after edits (uses charge_value when available)
        song_dicts = [
            {"rubric_color": s.rubric_color, "charge_value": s.charge_value, "position": s.position}
            for s in draft.songs
        ]
        draft.compass_degree = compute_degree(song_dicts)
        draft.charge_level = degree_to_charge(draft.compass_degree)
        draft.contamination_count = count_contaminated(
            [{"contaminated": s.contaminated} for s in draft.songs]
        )

    db.commit()
    db.refresh(draft)
    return draft


@router.post("/songs", response_model=SongOut, dependencies=[Depends(verify_admin_key)])
def feed_song(data: SongFeedIn, db: Session = Depends(get_db)):
    """Manually feed a song classification into the Song table.

    This serves two purposes:
    1. Training data for the agent (few-shot examples)
    2. Source for the public library

    All songs are stored regardless of tier — the library is non-opinionated.
    """
    current_year = data.year or date.today().year
    decade = f"{(current_year // 10) * 10}s"

    song = Song(
        title=data.title,
        artist=data.artist,
        year=current_year,
        decade=decade,
        chart_position=0,  # manual feed, no chart position
        rubric_color=data.rubric_color,
        charge_value=data.charge_value,
        contaminated=data.contaminated,
        contamination_note=data.contamination_note,
        charge_summary=data.charge_summary,
        message_analysis=data.message_analysis,
        expression_analysis=data.expression_analysis,
        intention_analysis=data.intention_analysis,
        chart_source=data.chart_source,
    )
    db.add(song)
    db.commit()
    db.refresh(song)
    return song
