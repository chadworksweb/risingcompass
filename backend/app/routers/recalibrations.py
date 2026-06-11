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
import re
import unicodedata
from datetime import datetime
from typing import Optional


def _slugify_rubric_change(note: str) -> str:
    """Derive a stable kebab-case slug from a plain-English rubric note.

    Takes the first ~8 words, lowercases, strips punctuation, collapses
    whitespace to single hyphens. Max 80 chars. Admins never see the slug —
    it's only the DB-level grouping key.
    """
    text = unicodedata.normalize("NFKD", note or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9\s]", "", text).lower().strip()
    words = text.split()[:8]
    slug = "-".join(words) or "rubric-change"
    return slug[:80]

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_

from sqlalchemy import text
from app.database import get_db, SessionLocal
from app.models import (
    SongRecalibration, SongRecalibrationProposal, Song,
    MisreadSubmission, CalibrationRun, SongSlug,
)
from app.routers.admin import verify_admin_key
from app.auth import verify_admin_or_lyrics_key
from app.services.agents.recalibrator import (
    recalibrate_song_satire, recalibrate_song_rubric_update,
)
from app.services.calibration_corpus import (
    hash_lyrics, log_run, _seed_initial_run_if_missing, _derive_tier,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/recalibrations", tags=["recalibrations"])

VALID_LENSES = {"standard", "satire"}
VALID_PIPELINES = {"manual", "rubric_update", "satirical_flag", "vibe_gap", "consensus_drift"}


def _resolve_song(db: Session, source: str, song_id: int) -> Song:
    """Resolve an incoming (source, song_id) to the unified Song. source='songs'
    is the unified id directly; a legacy pair maps via song_id_map."""
    if source == "songs":
        row = db.query(Song).get(song_id)
    else:
        nid = db.execute(
            text("SELECT new_song_id FROM song_id_map WHERE old_source = :s AND old_id = :i"),
            {"s": source, "i": song_id},
        ).scalar()
        row = db.query(Song).get(nid) if nid else None
    if not row:
        raise HTTPException(404, f"song {source}:{song_id} not found")
    return row


def _vibe_snapshot(db: Session, source: str, song_id: int) -> Optional[str]:
    """Snapshot the audience-vibe needle at the moment of recalibration.

    Returns a JSON string in the documented shape {value, pushes_up,
    pushes_down}, or None if the needle hasn't been touched yet (zero
    pushes recorded). Fails soft: any exception logs and returns None
    so a recalibration apply never blocks on a vibe read.
    """
    try:
        from app.services.audience_vibe import get_state
        state = get_state(db, source, song_id, device_id=None)
        if state.get("pushes_up_total", 0) == 0 and state.get("pushes_down_total", 0) == 0:
            return None
        return json.dumps({
            "value": state["value"],
            "pushes_up": state["pushes_up_total"],
            "pushes_down": state["pushes_down_total"],
        })
    except Exception:
        logger.exception("vibe_snapshot read failed for %s/%s", source, song_id)
        return None


def _flag_counts_for_song(db: Session, unified_id: int, title: str, artist: str) -> dict:
    """Snapshot of distinct-device flag counts at this moment."""
    title_l = (title or "").strip().lower()
    artist_l = (artist or "").strip().lower()
    match = or_(
        and_(
            func.lower(MisreadSubmission.song_title) == title_l,
            func.lower(MisreadSubmission.song_artist) == artist_l,
        ),
        MisreadSubmission.song_id == unified_id,
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

    For pipeline=rubric_update, send EITHER:
      - rubric_change_slug: selects an existing change (the admin's dropdown
        picked one that's already been applied to other songs), OR
      - rubric_change_note: a plain-English 1-2 sentence description. The
        server derives the slug from the note.
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
    # Lyrics are required to rewrite the listener + societal prose to the new
    # reading. Never persisted -- used transiently for the generation call and
    # discarded. The recalibrate UI forwards the same lyrics it ran against.
    lyrics: Optional[str] = None


class RejectIn(BaseModel):
    review_notes: Optional[str] = Field(None, max_length=4000)


class ApplySuppliedIn(BaseModel):
    """Apply a Claude-Code-supplied verdict to an already-published song.

    The verdict was produced OUT of band (by the calibration agent run through
    Claude Code, then human-ruled) -- the server runs NO model. This is the
    published-song analogue of correct_song.py (which is draft-only): it writes
    the supplied tier/charge straight onto the unified song, supersedes the
    prior runs, and logs the correction as a fresh calibration_run, exactly like
    accept_proposal -- but from a supplied verdict instead of a server proposal.
    Prose is intentionally NOT rewritten here (that would call the model); a
    follow-up prose regeneration is a separate, opt-in step.
    """
    song_source: str = "songs"
    song_id: int
    rubric_color: str = Field(..., description="violet | blue | green | orange | red")
    charge_value: int = Field(..., ge=-100, le=100)
    contaminated: bool = False
    contamination_note: Optional[str] = None
    charge_summary: Optional[str] = None
    # The agent's structured argument. Stored only after the lyric-quote scrub;
    # fails closed without lyrics (which are used for the check only, never kept).
    reasoning: Optional[str] = None
    lyrics: Optional[str] = None
    # The public-facing story of why the calibration changed (audit row).
    public_summary: str = Field(..., min_length=20, max_length=2000)
    internal_notes: Optional[str] = Field(None, max_length=4000)
    rubric_change_note: Optional[str] = Field(None, max_length=2000)
    agent_model: Optional[str] = "claude-code"


def _proposal_to_out(p: SongRecalibrationProposal, db: Session) -> dict:
    base = {
        "id": p.id,
        "lens": p.lens,
        "pipeline": p.pipeline,
        "song_source": "songs",
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
        song = _resolve_song(db, "songs", p.song_id)
        base["song_title"] = song.title
        base["song_artist"] = getattr(song, "artist", None)
    except HTTPException:
        pass
    return base


# --- Endpoints ---

@router.post("/start")
def start_recalibration(
    data: StartRecalibrateIn,
    db: Session = Depends(get_db),
    admin = Depends(verify_admin_key),
):
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
    # Normalize rubric-change inputs. Admin UIs pass plain-English notes;
    # existing slug selections pass the slug. For reuse we fill the missing
    # side from whichever source exists.
    rc_slug = (data.rubric_change_slug or "").strip() or None
    rc_note = (data.rubric_change_note or "").strip() or None
    if data.pipeline == "rubric_update":
        if not rc_slug and not rc_note:
            raise HTTPException(400, "Provide rubric_change_note (new) or rubric_change_slug (existing) when pipeline=rubric_update")
        if rc_slug and not rc_note:
            # Selected an existing change — look up its most recent note so
            # the audit row carries the canonical description.
            prior = (
                db.query(SongRecalibration.rubric_change_note)
                .filter(SongRecalibration.rubric_change_slug == rc_slug)
                .filter(SongRecalibration.rubric_change_note.isnot(None))
                .order_by(SongRecalibration.applied_at.desc())
                .first()
            )
            if prior and prior[0]:
                rc_note = prior[0]
            else:
                prior_p = (
                    db.query(SongRecalibrationProposal.rubric_change_note)
                    .filter(SongRecalibrationProposal.rubric_change_slug == rc_slug)
                    .filter(SongRecalibrationProposal.rubric_change_note.isnot(None))
                    .order_by(SongRecalibrationProposal.created_at.desc())
                    .first()
                )
                if prior_p and prior_p[0]:
                    rc_note = prior_p[0]
        if not rc_slug and rc_note:
            rc_slug = _slugify_rubric_change(rc_note)
        if not rc_note or len(rc_note) < 10:
            raise HTTPException(400, "rubric_change_note required (10+ chars) for new rubric changes")

    song = _resolve_song(db, data.song_source, data.song_id)
    unified_id = song.id
    original_color = song.rubric_color
    original_charge = song.charge_value
    original_summary = song.charge_summary

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
        song_id=unified_id,
        pipeline=data.pipeline,
        trigger_ref_id=data.trigger_ref_id,
        original_charge=original_charge,
        original_color=original_color,
        proposed_charge=result["charge_value"],
        proposed_color=result["rubric_color"],
        proposed_summary=result["charge_summary"],
        ai_rationale=result["ai_rationale"],
        ai_model=result["ai_model"],
        rubric_change_slug=rc_slug if data.pipeline == "rubric_update" else None,
        rubric_change_note=rc_note if data.pipeline == "rubric_update" else None,
        status="pending",
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)

    return _proposal_to_out(proposal, db)


@router.get("/pipelines/rubric-changes", dependencies=[Depends(verify_admin_key)])
def list_rubric_changes(db: Session = Depends(get_db)):
    """List all distinct rubric changes the admin can reuse.

    Each rubric change is identified by its slug. For each slug, we return
    the most recent note (the public-facing 1-2 sentence description) and
    the latest time it was used. Powers the rubric-change dropdown in the
    recalibrate UI so admins pick an existing change (or create a new one)
    without handling slug strings directly.
    """
    items = {}
    q = (
        db.query(
            SongRecalibration.rubric_change_slug,
            SongRecalibration.rubric_change_note,
            SongRecalibration.applied_at,
        )
        .filter(SongRecalibration.rubric_change_slug.isnot(None))
        .order_by(SongRecalibration.applied_at.desc())
    )
    for slug, note, applied_at in q.all():
        if not slug:
            continue
        if slug not in items:
            items[slug] = {
                "slug": slug,
                "note": note,
                "last_used": applied_at.isoformat() if applied_at else None,
            }
    # Include pending proposals too so a change in flight is reusable.
    pq = (
        db.query(
            SongRecalibrationProposal.rubric_change_slug,
            SongRecalibrationProposal.rubric_change_note,
            SongRecalibrationProposal.created_at,
        )
        .filter(SongRecalibrationProposal.rubric_change_slug.isnot(None))
        .order_by(SongRecalibrationProposal.created_at.desc())
    )
    for slug, note, created_at in pq.all():
        if not slug or slug in items:
            continue
        items[slug] = {
            "slug": slug,
            "note": note,
            "last_used": created_at.isoformat() if created_at else None,
        }
    return sorted(items.values(), key=lambda x: x["last_used"] or "", reverse=True)


@router.get("/pipelines/satirical-flags", dependencies=[Depends(verify_admin_key)])
def pending_satirical_flags(limit: int = 50, db: Session = Depends(get_db)):
    """Inbox for the satirical_flag pipeline. Returns pending satirical
    misread submissions, shaped for the recalibrate admin UI.

    Mirrors what /api/admin/misread?report_type=satirical returns, but
    lives on the recalibrations router so it doesn't require the public
    X-Api-Key header that the misread router is mounted under.
    """
    rows = (
        db.query(MisreadSubmission)
        .filter(MisreadSubmission.report_type == "satirical")
        .filter(MisreadSubmission.status == "pending")
        .order_by(MisreadSubmission.created_at.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    out = []
    for r in rows:
        out.append({
            "id": r.id,
            "song_title": r.song_title,
            "song_artist": r.song_artist,
            "song_source": "songs" if r.song_id else None,
            "song_id": r.song_id,
            "song_color": getattr(r, "song_color", None),
            "first_name": getattr(r, "first_name", None),
            "last_name": getattr(r, "last_name", None),
            "message": getattr(r, "message", None),
            "proof_context": getattr(r, "proof_context", None),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return out


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


def _regenerate_song_prose(unified_id: int, title: str, artist: str, lyrics: str) -> dict:
    """Rewrite listener + societal prose for a song after a recalibration.

    Every accepted recalibration rewrites BOTH prose fields. A recalibration
    (especially a satire-lens flip) changes how the lyrics are READ -- the
    meaning inverts even though the words are unchanged -- so prose written
    under the prior reading is now wrong. Mirrors the dedicated regeneration
    path (prose_admin.regenerate_prose): archive the old prose to prior_*,
    regenerate through the shared gap-fill, and re-stamp the provenance seal
    (the next anchor sweep publishes the new hash). Ether tags (deadpan_line /
    topics) are passed through, not regenerated -- only the two prose fields.

    Read -> generate -> write across three short sessions so the pooled
    connection is not held through the multi-second Opus calls. Returns
    {"listener": bool, "societal": bool} -- which fields were rewritten.
    """
    import asyncio
    from app.services.agents.calibrator import ensure_full_calibration

    # Phase 1: read current calibration + snapshot the prose we will replace.
    db = SessionLocal()
    try:
        song = db.get(Song, unified_id)
        if not song or not song.rubric_color:
            return {"listener": False, "societal": False}
        calibration = {
            "rubric_color": song.rubric_color,
            "charge_value": song.charge_value,
            "charge_summary": song.charge_summary,
            "contaminated": bool(getattr(song, "contaminated", False)),
            "contamination_note": getattr(song, "contamination_note", None),
            "deadpan_line": getattr(song, "deadpan_line", None),
            "topics": getattr(song, "topics", None),
            "topic_audit": getattr(song, "topic_audit", None),
            # Prose deliberately omitted -> the gap-fill regenerates both.
        }
        old_listener = song.listener_effects_prose
        old_societal = song.societal_effects_prose
        old_soc_at = song.societal_prose_generated_at
        old_soc_model = song.societal_prose_model
    finally:
        db.close()

    # Phase 2: generate (no DB session held during the Opus calls).
    asyncio.run(ensure_full_calibration(title, artist, lyrics, calibration))
    new_listener = calibration.get("listener_effects_prose")
    new_societal = calibration.get("societal_effects_prose")
    if not new_listener and not new_societal:
        return {"listener": False, "societal": False}

    # Phase 3: archive old, write new + reseal.
    db = SessionLocal()
    try:
        song = db.get(Song, unified_id)
        if not song:
            return {"listener": False, "societal": False}
        song.prior_listener_effects_prose = old_listener
        song.prior_societal_effects_prose = old_societal
        song.prior_societal_prose_generated_at = old_soc_at
        song.prior_societal_prose_model = old_soc_model
        if new_listener:
            song.listener_effects_prose = new_listener
        if new_societal:
            song.societal_effects_prose = new_societal
            song.societal_prose_generated_at = calibration.get("societal_prose_generated_at")
            song.societal_prose_model = calibration.get("societal_prose_model")
        db.commit()
    finally:
        db.close()

    return {"listener": bool(new_listener), "societal": bool(new_societal)}


@router.post("/{proposal_id}/accept")
def accept_proposal(
    proposal_id: int,
    data: AcceptIn,
    request: Request,
    db: Session = Depends(get_db),
    admin = Depends(verify_admin_key),
):
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

    song = _resolve_song(db, "songs", p.song_id)
    unified_id = song.id

    # Snapshot the public state at the moment of recalibration.
    flag_counts = _flag_counts_for_song(
        db, unified_id, song.title, song.artist or "",
    )
    vibe_snapshot = _vibe_snapshot(db, "songs", unified_id)

    # Snapshot the song's CURRENT summary before we overwrite it. The proposal
    # only carries before_charge/before_color; the summary lives on the song
    # record itself and would otherwise be lost on apply.
    before_summary = song.charge_summary

    audit = SongRecalibration(
        lens=p.lens,
        song_id=unified_id,
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
        vibe_snapshot=vibe_snapshot,
        rubric_change_slug=p.rubric_change_slug,
        rubric_change_note=p.rubric_change_note,
    )
    db.add(audit)

    # For rubric_update: seed a pre-mutation calibration_run if none exists
    # yet, so the original (pre-rubric-change) state stays visible as a
    # superseded entry in the public runs timeline. MUST run BEFORE we
    # mutate song below, or the seed captures the new values.
    if p.pipeline == "rubric_update":
        try:
            _seed_initial_run_if_missing("songs", song, db)
        except Exception:
            logger.exception("Failed to seed pre-rubric_update run for song %s", unified_id)

    # Apply to the song: charge, tier, AND the new charge_summary. A
    # recalibration changes how the lyrics are READ (a satire-lens flip inverts
    # the meaning even though the words are unchanged), so the proposed summary
    # is the new authoritative one-liner and replaces the prior literal one.
    # The listener + societal prose are rewritten too -- after the commit below,
    # via _regenerate_song_prose -- because prose written under the prior reading
    # is wrong once the reading moves. The re-seal on societal prose is intended:
    # the next anchor sweep publishes the new hash for the new text.
    song.rubric_color = p.proposed_color
    song.charge_value = p.proposed_charge
    if p.proposed_summary:
        song.charge_summary = p.proposed_summary
    recal_title = song.title
    recal_artist = song.artist or ""

    # Consensus restart on ANY accepted recalibration (2026-04-23): supersede
    # prior calibration_runs so the consensus engine starts fresh against the
    # new authoritative reading, and log the accepted calibration as the new
    # first run. Without this, old runs keep dragging consensus back toward
    # the pre-recalibration state (seen with satirical_flag: a 3rd agent run
    # after recalibration re-averaged against the old runs and mis-tiered).
    # Prior runs stay in the DB with superseded=True → visible in history,
    # excluded from consensus math.
    now = datetime.utcnow()
    slug = p.rubric_change_slug or p.pipeline
    prior = (
        db.query(CalibrationRun)
        .filter(CalibrationRun.song_id == unified_id)
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
        triggered_by=p.pipeline,
        song_id=unified_id,
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

    slug_row = (
        db.query(SongSlug)
        .filter(SongSlug.song_id == unified_id)
        .order_by(SongSlug.id.desc())
        .first()
    )
    song_slug = slug_row.slug if slug_row else None

    # Rewrite listener + societal prose to the new reading. Synchronous: the
    # admin waits (~30-60s) so the song is fully correct on return. Fail-soft --
    # the recalibration is already committed above, so any failure here leaves
    # the new charge applied with the prior prose intact.
    prose_rewritten = {"listener": False, "societal": False}
    lyrics = (data.lyrics or "").strip()
    if len(lyrics) >= 20:
        try:
            prose_rewritten = _regenerate_song_prose(unified_id, recal_title, recal_artist, lyrics)
        except Exception:
            logger.exception("post-recalibration prose rewrite failed for song %s", unified_id)
    else:
        logger.warning(
            "accept proposal %s: no lyrics supplied; prose NOT rewritten for song %s",
            p.id, unified_id,
        )

    return {
        "applied": True,
        "audit_id": audit.id,
        "song_source": "songs",
        "song_id": unified_id,
        "song_slug": song_slug,
        "before": {"charge": p.original_charge, "color": p.original_color},
        "after": {"charge": p.proposed_charge, "color": p.proposed_color},
        "flag_count_snapshot": flag_counts,
        "prose_rewritten": prose_rewritten,
    }


@router.post("/{proposal_id}/reject")
def reject_proposal(
    proposal_id: int,
    data: RejectIn,
    db: Session = Depends(get_db),
    admin = Depends(verify_admin_key),
):
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


@router.post("/apply-supplied")
def apply_supplied_correction(
    data: ApplySuppliedIn,
    request: Request,
    db: Session = Depends(get_db),
    _auth = Depends(verify_admin_or_lyrics_key),
):
    """Apply a Claude-Code-supplied verdict to a published song (no model call).

    Mirrors accept_proposal's apply block: seed a pre-mutation run if the song
    has none, write the supplied tier/charge/summary onto the unified song,
    supersede the prior non-superseded runs, and log the correction as the new
    calibration_run (with the agent argument, lyric-guarded). Writes an
    immutable SongRecalibration audit row (pipeline='manual', no proposal).
    Auth: admin session cookie OR X-Lyrics-Supply-Key (terminal).
    """
    color = (data.rubric_color or "").strip().lower()
    if color not in {"violet", "blue", "green", "orange", "red"}:
        raise HTTPException(400, "rubric_color must be one of violet|blue|green|orange|red")
    # Integrity: the supplied charge must fall in the supplied tier's band, and
    # orange/red can never be contaminated (contamination attaches to v/b/g only).
    derived = _derive_tier(data.charge_value)
    if derived != color:
        raise HTTPException(
            400,
            f"charge_value {data.charge_value} falls in '{derived}', not the supplied '{color}'",
        )
    if color in {"orange", "red"} and data.contaminated:
        raise HTTPException(400, "orange/red songs cannot be contaminated")

    song = _resolve_song(db, data.song_source, data.song_id)
    unified_id = song.id
    before_charge = song.charge_value
    before_color = song.rubric_color
    before_summary = song.charge_summary

    flag_counts = _flag_counts_for_song(db, unified_id, song.title, song.artist or "")
    vibe_snapshot = _vibe_snapshot(db, "songs", unified_id)

    rc_note = (data.rubric_change_note or "").strip() or None
    rc_slug = _slugify_rubric_change(rc_note) if rc_note else None
    supersede_reason = rc_slug or "manual"

    # Seed the pre-correction state as a run BEFORE mutating, so the original
    # value stays visible as a superseded entry in the timeline.
    try:
        _seed_initial_run_if_missing("songs", song, db)
    except Exception:
        logger.exception("Failed to seed pre-correction run for song %s", unified_id)

    # Apply supplied verdict to the song.
    song.rubric_color = color
    song.charge_value = data.charge_value
    if data.charge_summary:
        song.charge_summary = data.charge_summary
    song.contaminated = bool(data.contaminated)
    song.contamination_note = data.contamination_note if data.contaminated else None

    # Supersede prior live runs so consensus restarts against the new verdict.
    now = datetime.utcnow()
    prior = (
        db.query(CalibrationRun)
        .filter(CalibrationRun.song_id == unified_id)
        .filter(CalibrationRun.superseded.is_(False))
        .all()
    )
    for r in prior:
        r.superseded = True
        r.superseded_reason = supersede_reason
        r.superseded_at = now

    run = log_run(
        db,
        title=song.title,
        artist=getattr(song, "artist", None),
        calibration={
            "rubric_color": color,
            "charge_value": data.charge_value,
            "charge_summary": data.charge_summary or song.charge_summary,
            "contaminated": bool(data.contaminated),
            "contamination_note": song.contamination_note,
            "confidence": 1.0,
            "reasoning": data.reasoning,
        },
        triggered_by="manual",
        song_id=unified_id,
        lyrics_hash=hash_lyrics(data.lyrics) if data.lyrics else None,
        agent_model=data.agent_model,
        lyrics=data.lyrics,
    )

    audit = SongRecalibration(
        lens="standard",
        song_id=unified_id,
        proposal_id=None,
        pipeline="manual",
        before_charge=before_charge,
        before_color=before_color,
        before_summary=before_summary,
        after_charge=data.charge_value,
        after_color=color,
        ai_rationale=None,
        public_summary=data.public_summary,
        internal_notes=data.internal_notes,
        human_rationale=data.internal_notes,
        flag_count_snapshot=json.dumps(flag_counts),
        vibe_snapshot=vibe_snapshot,
        rubric_change_slug=rc_slug,
        rubric_change_note=rc_note,
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)

    return {
        "applied": True,
        "audit_id": audit.id,
        "run_id": run.id,
        "song_id": unified_id,
        "title": song.title,
        "before": {"charge": before_charge, "color": before_color},
        "after": {"charge": data.charge_value, "color": color},
        "superseded_runs": len(prior),
        "reasoning_stored": bool(run.reasoning),
        "prose_rewritten": False,
    }


# --- Calibrator era boundary (v3 deploy mechanism) --------------------------

class EraSupersedeIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=100)
    confirm: bool = False


@router.post("/era-supersede", dependencies=[Depends(verify_admin_key)])
def era_supersede(data: EraSupersedeIn, db: Session = Depends(get_db)):
    """Corpus-wide rubric-era boundary: supersede EVERY live calibration run.

    Deploying a new calibrator is an era boundary -- "the measuring stick
    moved, read it fresh." This mirrors the per-song supersede that every
    accepted recalibration already performs, applied corpus-wide exactly once
    on deploy day. Prior runs stay in the ledger (visible in history) but stop
    voting in consensus, and live_run_count resets to zero everywhere, which
    reopens every public-capped song to the Lyrical Charger.

    Deliberately an ADMIN ENDPOINT and not a migration: local dev runs
    migrations against the shared prod DB through the tunnel, so a startup
    migration would trip the boundary while prod still ran the old calibrator.
    This fires only when called, after the new code is live.

    Does NOT touch songs rows -- no mass re-read; canonical values stand until
    organic re-reads (daily reading, Lyrical Charger, audits) move them.
    Idempotent: a second call finds nothing live and supersedes zero rows.
    Requires confirm=true.
    """
    if not data.confirm:
        raise HTTPException(400, "confirm=true is required to move the era boundary")
    reason = data.reason.strip()
    now = datetime.utcnow()
    updated = (
        db.query(CalibrationRun)
        .filter(CalibrationRun.superseded.is_(False))
        .update({
            CalibrationRun.superseded: True,
            CalibrationRun.superseded_reason: reason,
            CalibrationRun.superseded_at: now,
        }, synchronize_session=False)
    )
    db.commit()
    logger.warning(
        "ERA BOUNDARY: superseded %d live calibration runs (reason=%s)",
        updated, reason,
    )
    return {"superseded_runs": int(updated), "reason": reason,
            "at": now.isoformat()}
