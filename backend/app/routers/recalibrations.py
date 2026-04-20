"""Admin recalibration suite — one mechanism, many pipelines.

There is ONE recalibrate mechanism (pick song → agent re-reads → admin
reviews + accepts). Pipelines are the DIFFERENT TRIGGERS that feed
into it. The LENS is how the agent re-reads (standard or satire).

Workflow:
  1. POST /api/admin/recalibrations/start — admin (or UI, triggered by a
     pipeline item) submits {song, lyrics, pipeline, lens, pipeline-specific
     metadata}. Server runs the agent under the chosen lens and persists
     a proposal.
  2. Admin reviews the proposal (charge, rationale, reasoning).
  3. POST /api/admin/recalibrations/{id}/accept — admin writes a public
     summary; server applies the new charge, writes the audit row, and
     runs pipeline-specific side-effects (close flag, supersede prior runs).
  4. POST /api/admin/recalibrations/{id}/reject — admin closes the
     proposal with review notes; nothing is applied.

Pipelines (current + planned): manual, rubric_update, satirical_flag,
vibe_gap, consensus_drift (write-only from the corpus engine).

Lenses: standard (plain rubric), satire (satire lens overlay).
"""

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_

from app.database import get_db
from app.models import (
    SongRecalibration, SongRecalibrationProposal,
    CompassSong, LibrarySong, SubmittedSong, StreamSong,
    MisreadSubmission, CalibrationRun,
)
from app.routers.admin import verify_admin_key
from app.services.agents.recalibrator import (
    recalibrate_song_satire, recalibrate_song_rubric_update,
)
from app.services.calibration_corpus import hash_lyrics, log_run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/recalibrations", tags=["recalibrations"])

VALID_LENSES = {"standard", "satire"}
VALID_PIPELINES = {"manual", "rubric_update", "satirical_flag", "vibe_gap", "consensus_drift"}

SONG_MODELS = {
    "compass": CompassSong,
    "library": LibrarySong,
    "submitted": SubmittedSong,
    "stream": StreamSong,
}


def _resolve_song(db: Session, source: str, song_id: int):
    model = SONG_MODELS.get(source)
    if not model:
        raise HTTPException(400, f"Invalid song_source. Must be one of: {', '.join(SONG_MODELS)}")
    row = db.query(model).get(song_id)
    if not row:
        raise HTTPException(404, f"{source} song id={song_id} not found")
    return row


def _flag_counts_for_song(db: Session, source: str, song_id: int, title: str, artist: str) -> dict:
    """Snapshot of distinct-device flag counts at this moment."""
    title_l = (title or "").strip().lower()
    artist_l = (artist or "").strip().lower()
    match = or_(
        and_(
            func.lower(MisreadSubmission.song_title) == title_l,
            func.lower(MisreadSubmission.song_artist) == artist_l,
        ),
        and_(
            MisreadSubmission.song_source == source,
            MisreadSubmission.song_id == song_id,
        ),
    )

    def _count(report_type: str) -> int:
        return db.query(func.count(func.distinct(MisreadSubmission.device_id))).filter(
            match,
            MisreadSubmission.report_type == report_type,
            MisreadSubmission.device_id.isnot(None),
        ).scalar() or 0

    return {"misread": _count("misread"), "satirical": _count("satirical")}


# --- Schemas ---

class StartRecalibrateIn(BaseModel):
    """Unified start request. Pipeline says what triggered it; lens says how
    the agent re-reads. Pipeline-specific fields are optional and validated
    conditionally below.
    """
    song_source: str
    song_id: int
    lyrics: str = Field(..., min_length=20)
    pipeline: str = Field(..., description="manual | rubric_update | satirical_flag | vibe_gap")
    lens: str = Field(default="standard", description="standard | satire")
    # pipeline=rubric_update:
    rubric_change_slug: Optional[str] = Field(None, max_length=100)
    rubric_change_note: Optional[str] = Field(None, max_length=2000)
    # pipeline=satirical_flag:
    trigger_ref_id: Optional[int] = None  # misread_submissions.id


class AcceptIn(BaseModel):
    public_summary: str = Field(..., min_length=20, max_length=2000)
    internal_notes: Optional[str] = Field(None, max_length=4000)


class RejectIn(BaseModel):
    review_notes: Optional[str] = Field(None, max_length=4000)


def _proposal_to_out(p: SongRecalibrationProposal, db: Session) -> dict:
    base = {
        "id": p.id,
        "lens": p.lens,
        "pipeline": p.pipeline,
        "song_source": p.song_source,
        "song_id": p.song_id,
        "trigger_ref_id": p.trigger_ref_id,
        "original_charge": p.original_charge,
        "original_color": p.original_color,
        "proposed_charge": p.proposed_charge,
        "proposed_color": p.proposed_color,
        "proposed_summary": p.proposed_summary,
        "ai_rationale": p.ai_rationale,
        "ai_model": p.ai_model,
        "status": p.status,
        "review_notes": p.review_notes,
        "rubric_change_slug": p.rubric_change_slug,
        "rubric_change_note": p.rubric_change_note,
        "created_at": p.created_at,
        "resolved_at": p.resolved_at,
    }
    try:
        song = _resolve_song(db, p.song_source, p.song_id)
        base["song_title"] = song.title
        base["song_artist"] = getattr(song, "artist", None)
    except HTTPException:
        pass
    return base


# --- Endpoints ---

@router.post("/start", dependencies=[Depends(verify_admin_key)])
def start_recalibration(data: StartRecalibrateIn, db: Session = Depends(get_db)):
    """Unified entrypoint. One mechanism; pipeline + lens parameterize it.

    Runs the agent under the chosen lens and persists a pending proposal.
    Admin then reviews the proposal and calls /accept or /reject.
    """
    if data.pipeline not in VALID_PIPELINES:
        raise HTTPException(400, f"Invalid pipeline. Must be one of: {sorted(VALID_PIPELINES)}")
    if data.lens not in VALID_LENSES:
        raise HTTPException(400, f"Invalid lens. Must be one of: {sorted(VALID_LENSES)}")
    if data.pipeline == "consensus_drift":
        raise HTTPException(400, "consensus_drift is written by the corpus engine, not started by admins")
    if data.pipeline == "rubric_update":
        if not data.rubric_change_slug or len(data.rubric_change_slug) < 3:
            raise HTTPException(400, "rubric_change_slug required (3+ chars) when pipeline=rubric_update")
        if not data.rubric_change_note or len(data.rubric_change_note) < 10:
            raise HTTPException(400, "rubric_change_note required (10+ chars) when pipeline=rubric_update")

    song = _resolve_song(db, data.song_source, data.song_id)
    original_color = getattr(song, "rubric_color", None)
    original_charge = getattr(song, "charge_value", None)
    original_summary = getattr(song, "charge_summary", None)

    try:
        if data.lens == "satire":
            result = recalibrate_song_satire(
                title=song.title,
                artist=getattr(song, "artist", "") or "",
                lyrics=data.lyrics,
                db=db,
                original_color=original_color,
                original_charge=original_charge,
                original_summary=original_summary,
            )
        else:
            result = recalibrate_song_rubric_update(
                title=song.title,
                artist=getattr(song, "artist", "") or "",
                lyrics=data.lyrics,
                db=db,
            )
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    proposal = SongRecalibrationProposal(
        lens=data.lens,
        song_source=data.song_source,
        song_id=data.song_id,
        pipeline=data.pipeline,
        trigger_ref_id=data.trigger_ref_id,
        original_charge=original_charge,
        original_color=original_color,
        proposed_charge=result["charge_value"],
        proposed_color=result["rubric_color"],
        proposed_summary=result["charge_summary"],
        ai_rationale=result["ai_rationale"],
        ai_model=result["ai_model"],
        rubric_change_slug=data.rubric_change_slug if data.pipeline == "rubric_update" else None,
        rubric_change_note=data.rubric_change_note if data.pipeline == "rubric_update" else None,
        status="pending",
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)

    return _proposal_to_out(proposal, db)


@router.get("", dependencies=[Depends(verify_admin_key)])
def list_proposals(
    status: Optional[str] = None,
    pipeline: Optional[str] = None,
    lens: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    q = db.query(SongRecalibrationProposal)
    if status:
        q = q.filter(SongRecalibrationProposal.status == status)
    if pipeline:
        if pipeline not in VALID_PIPELINES:
            raise HTTPException(400, f"Invalid pipeline")
        q = q.filter(SongRecalibrationProposal.pipeline == pipeline)
    if lens:
        if lens not in VALID_LENSES:
            raise HTTPException(400, f"Invalid lens")
        q = q.filter(SongRecalibrationProposal.lens == lens)
    rows = q.order_by(SongRecalibrationProposal.created_at.desc()).limit(limit).all()
    return [_proposal_to_out(p, db) for p in rows]


@router.get("/{proposal_id}", dependencies=[Depends(verify_admin_key)])
def get_proposal(proposal_id: int, db: Session = Depends(get_db)):
    p = db.query(SongRecalibrationProposal).get(proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    return _proposal_to_out(p, db)


@router.post("/{proposal_id}/accept", dependencies=[Depends(verify_admin_key)])
def accept_proposal(proposal_id: int, data: AcceptIn, db: Session = Depends(get_db)):
    """Apply the proposal: write the new charge to the song, append an
    immutable audit row, mark the proposal accepted, and close any related
    satirical flag.
    """
    p = db.query(SongRecalibrationProposal).get(proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    if p.status != "pending":
        raise HTTPException(400, f"Proposal is not pending (status={p.status})")
    if p.proposed_charge is None or not p.proposed_color:
        raise HTTPException(400, "Proposal has no proposed values to apply")

    song = _resolve_song(db, p.song_source, p.song_id)

    # Snapshot the public state at the moment of recalibration. Vibe snapshot
    # is reserved for when the vibe layer ships — schema slot already exists.
    flag_counts = _flag_counts_for_song(
        db, p.song_source, p.song_id, song.title, getattr(song, "artist", "") or "",
    )

    # Snapshot the song's CURRENT summary before we overwrite it. The proposal
    # only carries before_charge/before_color; the summary lives on the song
    # record itself and would otherwise be lost on apply.
    before_summary = getattr(song, "charge_summary", None)

    audit = SongRecalibration(
        lens=p.lens,
        song_source=p.song_source,
        song_id=p.song_id,
        proposal_id=p.id,
        pipeline=p.pipeline,
        trigger_ref_id=p.trigger_ref_id,
        before_charge=p.original_charge,
        before_color=p.original_color,
        before_summary=before_summary,
        after_charge=p.proposed_charge,
        after_color=p.proposed_color,
        ai_rationale=p.ai_rationale,
        public_summary=data.public_summary,
        internal_notes=data.internal_notes,
        flag_count_snapshot=json.dumps(flag_counts),
        rubric_change_slug=p.rubric_change_slug,
        rubric_change_note=p.rubric_change_note,
    )
    db.add(audit)

    # Apply to the song.
    song.rubric_color = p.proposed_color
    song.charge_value = p.proposed_charge
    if p.proposed_summary:
        song.charge_summary = p.proposed_summary

    # rubric_update side-effects: supersede prior calibration_runs so the
    # consensus engine excludes them, then log the accepted calibration as
    # a fresh run under the new rubric. Without this, the canonical row
    # would drift back toward the pre-update consensus as new runs arrive.
    # Other pipelines (satirical_flag, manual, vibe_gap) do NOT supersede —
    # they're targeted re-reads that coexist with history.
    if p.pipeline == "rubric_update":
        now = datetime.utcnow()
        slug = p.rubric_change_slug or "rubric_update"
        prior = (
            db.query(CalibrationRun)
            .filter(CalibrationRun.song_source == p.song_source)
            .filter(CalibrationRun.song_id == p.song_id)
            .filter(CalibrationRun.superseded.is_(False))
            .all()
        )
        for r in prior:
            r.superseded = True
            r.superseded_reason = slug
            r.superseded_at = now

        log_run(
            db,
            title=song.title,
            artist=getattr(song, "artist", None),
            calibration={
                "rubric_color": p.proposed_color,
                "charge_value": p.proposed_charge,
                "charge_summary": p.proposed_summary,
                "contaminated": bool(getattr(song, "contaminated", False)),
                "contamination_note": getattr(song, "contamination_note", None),
                "confidence": 1.0,
            },
            triggered_by="rubric_update",
            song_source=p.song_source,
            song_id=p.song_id,
            lyrics_hash=None,
            agent_model=p.ai_model,
        )

    # Resolve the proposal.
    p.status = "accepted"
    p.review_notes = data.internal_notes
    p.resolved_at = datetime.utcnow()

    # Close the originating satirical flag, if any.
    if p.pipeline == "satirical_flag" and p.trigger_ref_id:
        flag = db.query(MisreadSubmission).get(p.trigger_ref_id)
        if flag:
            flag.status = "accepted"

    db.commit()
    db.refresh(audit)

    return {
        "applied": True,
        "audit_id": audit.id,
        "song_source": p.song_source,
        "song_id": p.song_id,
        "before": {"charge": p.original_charge, "color": p.original_color},
        "after": {"charge": p.proposed_charge, "color": p.proposed_color},
        "flag_count_snapshot": flag_counts,
    }


@router.post("/{proposal_id}/reject", dependencies=[Depends(verify_admin_key)])
def reject_proposal(proposal_id: int, data: RejectIn, db: Session = Depends(get_db)):
    p = db.query(SongRecalibrationProposal).get(proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    if p.status != "pending":
        raise HTTPException(400, f"Proposal is not pending (status={p.status})")

    p.status = "rejected"
    p.review_notes = data.review_notes
    p.resolved_at = datetime.utcnow()

    # Close the originating satirical flag as rejected too — admin investigated
    # and chose not to apply. The flag stays in the queue with a clear status.
    if p.pipeline == "satirical_flag" and p.trigger_ref_id:
        flag = db.query(MisreadSubmission).get(p.trigger_ref_id)
        if flag and flag.status == "pending":
            flag.status = "rejected"

    db.commit()
    return {"applied": False, "proposal_id": p.id, "status": p.status}
