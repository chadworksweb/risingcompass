"""Admin endpoints for the Compass Agent — trigger, list, view, approve, reject, edit drafts."""

import logging
import math
from datetime import date

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AgentDraft, AgentDraftSong, DailyReading, ReadingSong, CompassSong
from app.schemas import (
    BackfillResult, BackfillSongOut,
    CalibrateRequest, CalibrateResult,
    DraftOut, DraftTriggerIn, DraftUpdate,
    PaginatedDrafts, DraftSummary, CompassSongFeedIn, CompassSongOut,
)
from app.config import settings
from app.routers.admin import verify_admin_key
from app.services.agents.compass_agent import run_compass_agent
from app.services.agents.chart_source import fetch_top_songs
from app.services.agents.classifier import classify_song
from app.services.agents.email_notifier import send_draft_email
from app.services.agents.lyrics_source import fetch_lyrics
from app.services.compass_calc import compute_degree
from app.services.charge_calc import degree_to_charge
from app.services.contamination import count_contaminated
from app.constants import COLOR_LABELS, COLOR_HEX

router = APIRouter(prefix="/api/admin/agent", tags=["agent"])


def _resolve_draft(draft_ref: str, db: Session) -> AgentDraft:
    """Look up a draft by label or numeric ID. Raises 404 if not found."""
    # Try label first
    draft = db.query(AgentDraft).filter(AgentDraft.label == draft_ref).first()
    if draft:
        return draft
    # Fall back to numeric ID for backwards compat
    try:
        draft_id = int(draft_ref)
        draft = db.query(AgentDraft).filter(AgentDraft.id == draft_id).first()
    except ValueError:
        pass
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


def _degree_to_score(degree: float) -> str:
    score = round((90 - degree) * 100 / 90)
    return f"{'+' if score > 0 else ''}{score}"


def _build_approval_html(draft) -> str:
    """Build a styled HTML confirmation page after approving a draft."""
    charge_color = COLOR_HEX.get(draft.charge_level, "#999")
    charge_label = COLOR_LABELS.get(draft.charge_level, draft.charge_level)
    score = _degree_to_score(draft.compass_degree)
    song_count = len(draft.songs) if draft.songs else 0

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reading Published — {draft.date}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#0a0a14; color:#eeeef4; font-family:'Inter',-apple-system,sans-serif; min-height:100vh; display:flex; align-items:center; justify-content:center; }}
  .card {{ background:#1a1a2e; border-radius:12px; padding:48px; max-width:480px; width:90%; text-align:center; border:1px solid #2a2a4e; }}
  .check {{ width:64px; height:64px; border-radius:50%; background:rgba(0,212,170,0.12); display:flex; align-items:center; justify-content:center; margin:0 auto 24px; }}
  .check svg {{ width:32px; height:32px; color:#00d4aa; }}
  h1 {{ font-size:22px; font-weight:600; margin-bottom:8px; }}
  .date {{ font-size:14px; color:#88ccaa; margin-bottom:32px; }}
  .metrics {{ display:flex; justify-content:center; gap:32px; margin-bottom:32px; }}
  .metric {{ text-align:center; }}
  .metric-label {{ font-size:10px; text-transform:uppercase; letter-spacing:0.1em; color:#666; margin-bottom:6px; }}
  .metric-value {{ font-family:'JetBrains Mono',monospace; font-size:28px; font-weight:700; }}
  .editorial {{ font-size:14px; line-height:1.6; color:#aaa; font-style:italic; margin-bottom:32px; padding:0 8px; }}
  .meta {{ font-size:11px; color:#555; }}
</style>
</head><body>
<div class="card">
  <div class="check">
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
      <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>
    </svg>
  </div>
  <h1>Reading Published</h1>
  <div class="date">{draft.date}</div>
  <div class="metrics">
    <div class="metric">
      <div class="metric-label">Charge</div>
      <div class="metric-value" style="color:{charge_color};">{score}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Level</div>
      <div class="metric-value" style="color:{charge_color};">{charge_label}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Songs</div>
      <div class="metric-value" style="color:#eeeef4;">{song_count}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Contaminated</div>
      <div class="metric-value" style="color:{'#ff3333' if draft.contamination_count > 0 else '#00d4aa'};">{draft.contamination_count}</div>
    </div>
  </div>
  <div class="editorial">{draft.editorial_summary or ''}</div>
  <div class="meta">Agent: {draft.agent_model or 'unknown'} &middot; The Rising Compass</div>
</div>
</body></html>"""


@router.post("/classify", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def trigger_classification(data: DraftTriggerIn, db: Session = Depends(get_db)):
    """Trigger classification on a list of songs (calibration mode).

    Creates an AgentDraft with Claude-generated classifications.
    """
    songs_input = [s.model_dump() for s in data.songs]
    reading_date = data.date or date.today()

    draft = run_compass_agent(songs_input, db, reading_date=reading_date, draft_only=data.draft_only, draft_type="manual")
    return draft


@router.post("/classify-live", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def classify_live(db: Session = Depends(get_db)):
    """Fetch today's Spotify Top 50 USA and classify them.

    The chart is pinned: once fetched for a given day, subsequent calls
    reuse the same song list (from the first draft) so the reading is
    stable regardless of intra-day playlist shuffling.
    """
    today = date.today()

    # Pin chart: reuse song list from first daily draft of the day if one exists
    existing_draft = (
        db.query(AgentDraft)
        .filter(AgentDraft.date == today)
        .filter(AgentDraft.draft_type == "daily")
        .order_by(AgentDraft.id.asc())
        .first()
    )
    chart_pinned = False
    if existing_draft and existing_draft.songs:
        songs = [
            {
                "title": s.title,
                "artist": s.artist,
                "position": s.position,
                "chart_source": s.chart_source or "spotify_top50_usa",
            }
            for s in sorted(existing_draft.songs, key=lambda s: s.position)
        ]
        chart_pinned = True
        logger.info("Chart pinned: reusing %d songs from %s", len(songs), existing_draft.label)
    else:
        songs = fetch_top_songs(count=20)
        if not songs:
            raise HTTPException(status_code=502, detail="Failed to fetch chart data from Spotify")

    draft = run_compass_agent(songs, db, reading_date=today, draft_type="daily")

    # Surface chart pinning in agent_notes and warnings
    if chart_pinned:
        pin_msg = f"chart_pinned: reusing {len(songs)} songs from {existing_draft.label}"
        # Append to agent_notes
        if draft.agent_notes:
            draft.agent_notes += f"; {pin_msg}"
        else:
            draft.agent_notes = pin_msg
        # Append to warnings
        import json
        existing_warnings = json.loads(draft.agent_warnings) if draft.agent_warnings else []
        existing_warnings.append(pin_msg)
        draft.agent_warnings = json.dumps(existing_warnings)
        db.commit()
        db.refresh(draft)

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


@router.get("/drafts/{draft_ref}", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def get_draft(draft_ref: str, db: Session = Depends(get_db)):
    """Get a single draft with all songs. Accepts label or numeric ID."""
    return _resolve_draft(draft_ref, db)


@router.get("/drafts/{draft_ref}/approve", response_class=HTMLResponse)
def approve_draft_confirm_page(draft_ref: str, key: str = Query(...), db: Session = Depends(get_db)):
    """Show a confirmation page instead of auto-approving.

    Email clients prefetch GET links for security scanning, which was
    causing drafts to be approved without human intent. Now the GET
    shows a confirm button that POSTs to actually approve.
    """
    if key != settings.rc_admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    draft = _resolve_draft(draft_ref, db)

    if draft.status != "pending":
        return _build_approval_html(draft)

    charge_color = COLOR_HEX.get(draft.charge_level, "#999")
    charge_label = COLOR_LABELS.get(draft.charge_level, draft.charge_level)
    score = _degree_to_score(draft.compass_degree)
    post_url = f"/api/admin/agent/drafts/{draft_ref}/approve?key={key}"

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Confirm Reading — {draft.date}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#0a0a14; color:#eeeef4; font-family:'Inter',-apple-system,sans-serif; min-height:100vh; display:flex; align-items:center; justify-content:center; }}
  .card {{ background:#1a1a2e; border-radius:12px; padding:48px; max-width:480px; width:90%; text-align:center; border:1px solid #2a2a4e; }}
  h1 {{ font-size:22px; font-weight:600; margin-bottom:8px; }}
  .date {{ font-size:14px; color:#88ccaa; margin-bottom:24px; }}
  .metrics {{ display:flex; justify-content:center; gap:32px; margin-bottom:32px; }}
  .metric {{ text-align:center; }}
  .metric-label {{ font-size:10px; text-transform:uppercase; letter-spacing:0.1em; color:#666; margin-bottom:6px; }}
  .metric-value {{ font-family:'JetBrains Mono',monospace; font-size:28px; font-weight:700; }}
  .btn {{ display:inline-block; padding:14px 48px; background:#00d4aa; color:#0a0a14; font-weight:700; font-size:16px; border:none; border-radius:8px; cursor:pointer; text-decoration:none; margin-top:8px; }}
  .btn:hover {{ background:#00eebb; }}
  .label {{ font-size:12px; color:#555; margin-top:20px; font-family:'JetBrains Mono',monospace; }}
</style>
</head><body>
<div class="card">
  <h1>Publish This Reading?</h1>
  <div class="date">{draft.date}</div>
  <div class="metrics">
    <div class="metric">
      <div class="metric-label">Charge</div>
      <div class="metric-value" style="color:{charge_color};">{score}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Level</div>
      <div class="metric-value" style="color:{charge_color};">{charge_label}</div>
    </div>
  </div>
  <form method="POST" action="/api/admin/agent/drafts/{draft_ref}/publish?key={key}">
    <button type="submit" class="btn">Approve &amp; Publish</button>
  </form>
  <div class="label">{draft.label}</div>
</div>
</body></html>"""


@router.post("/drafts/{draft_ref}/publish", response_class=HTMLResponse)
def publish_draft_via_form(draft_ref: str, key: str = Query(...), db: Session = Depends(get_db)):
    """POST approval from the email confirmation page (form submit with key in query)."""
    if key != settings.rc_admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    draft = approve_draft(draft_ref, db)
    return _build_approval_html(draft)


@router.post("/drafts/{draft_ref}/approve", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def approve_draft(draft_ref: str, db: Session = Depends(get_db)):
    """Approve a draft and publish it as a DailyReading."""
    draft = _resolve_draft(draft_ref, db)
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
            message_analysis=song.message_analysis,
            expression_analysis=song.expression_analysis,
            intention_analysis=song.intention_analysis,
            chart_source=song.chart_source,
        )
        db.add(rs)

    draft.status = "approved"
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/drafts/{draft_ref}/reject", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def reject_draft(draft_ref: str, db: Session = Depends(get_db)):
    """Mark a draft as rejected."""
    draft = _resolve_draft(draft_ref, db)
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail=f"Draft is already {draft.status}")

    draft.status = "rejected"
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/drafts/{draft_ref}/resend-email", dependencies=[Depends(verify_admin_key)])
def resend_draft_email(draft_ref: str, db: Session = Depends(get_db)):
    """Resend the notification email for an existing draft."""
    from app.config import settings

    draft = _resolve_draft(draft_ref, db)

    sent = send_draft_email(draft, draft.songs, settings, db=db)
    if not sent:
        raise HTTPException(status_code=500, detail="Email failed — check SMTP config")
    return {"status": "sent", "to": settings.approval_email}


@router.put("/drafts/{draft_ref}", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def update_draft(draft_ref: str, data: DraftUpdate, db: Session = Depends(get_db)):
    """Edit a draft before approving — change colors, summaries, etc."""
    draft = _resolve_draft(draft_ref, db)
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

            # Enforce: red/orange cannot be contaminated
            if existing.rubric_color in ("red", "orange"):
                existing.contaminated = False
                existing.contamination_note = None

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


@router.post("/songs", response_model=CompassSongOut, dependencies=[Depends(verify_admin_key)])
def feed_song(data: CompassSongFeedIn, db: Session = Depends(get_db)):
    """Manually feed a song classification into the CompassSong table.

    This serves two purposes:
    1. Training data for the agent (few-shot examples)
    2. Source for the public library

    All songs are stored regardless of tier — the library is non-opinionated.
    """
    current_year = data.year or date.today().year
    decade = f"{(current_year // 10) * 10}s"

    song = CompassSong(
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


@router.post("/backfill/{year}", response_model=BackfillResult, dependencies=[Depends(verify_admin_key)])
def backfill_year(
    year: int,
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Reclassify uncalibrated songs for a given year using the 5-tier rubric.

    Only processes songs that are NOT calibrated. Fetches lyrics and runs
    the full classification pipeline. Updates the CompassSong table in place.

    Args:
        year: The year to backfill (e.g. 1965).
        limit: Max songs to process per call (default 10, max 50).
    """
    # Get uncalibrated songs for this year
    uncalibrated = (
        db.query(CompassSong)
        .filter(CompassSong.year == year)
        .filter(CompassSong.calibrated.is_(False))
        .limit(limit)
        .all()
    )

    calibrated_count = (
        db.query(CompassSong)
        .filter(CompassSong.year == year)
        .filter(CompassSong.calibrated.is_(True))
        .count()
    )

    total_for_year = db.query(CompassSong).filter(CompassSong.year == year).count()

    results = []
    for song in uncalibrated:
        # Fetch lyrics
        lyrics = fetch_lyrics(song.title, song.artist)
        lyrics_available = lyrics is not None

        # Classify fresh — skip cache but use db for few-shot examples
        result = classify_song(song.title, song.artist, lyrics=lyrics, db=db, target_year=year, skip_cache=True)

        # Update song in place
        song.rubric_color = result["rubric_color"]
        song.charge_value = result.get("charge_value")
        song.contaminated = result["contaminated"]
        song.contamination_note = result.get("contamination_note")
        song.charge_summary = result.get("charge_summary")
        song.message_analysis = result.get("message_analysis")
        song.expression_analysis = result.get("expression_analysis")
        song.intention_analysis = result.get("intention_analysis")
        # Do NOT mark calibrated — human must review first

        results.append(BackfillSongOut(
            id=song.id,
            title=song.title,
            artist=song.artist,
            year=song.year,
            rubric_color=result["rubric_color"],
            charge_value=result.get("charge_value"),
            contaminated=result["contaminated"],
            contamination_note=result.get("contamination_note"),
            charge_summary=result.get("charge_summary"),
            message_analysis=result.get("message_analysis"),
            expression_analysis=result.get("expression_analysis"),
            intention_analysis=result.get("intention_analysis"),
            confidence=result.get("confidence"),
            lyrics_available=lyrics_available,
        ))

    db.commit()

    return BackfillResult(
        year=year,
        total_songs=total_for_year,
        reclassified=len(results),
        skipped_calibrated=calibrated_count,
        songs=results,
    )


@router.post("/calibrate", response_model=CalibrateResult, dependencies=[Depends(verify_admin_key)])
def calibrate_songs(data: CalibrateRequest, db: Session = Depends(get_db)):
    """Apply human-calibrated tiers and charge values to songs, marking them calibrated.

    Takes a list of song IDs with corrected rubric_color and charge_value.
    Updates the CompassSong table and sets calibrated=True.
    """
    updated = []
    for item in data.songs:
        song = db.query(CompassSong).filter(CompassSong.id == item.id).first()
        if not song:
            raise HTTPException(status_code=404, detail=f"Song ID {item.id} not found")
        song.rubric_color = item.rubric_color
        song.charge_value = item.charge_value
        if item.charge_summary is not None:
            song.charge_summary = item.charge_summary
        if item.message_analysis is not None:
            song.message_analysis = item.message_analysis
        if item.expression_analysis is not None:
            song.expression_analysis = item.expression_analysis
        if item.intention_analysis is not None:
            song.intention_analysis = item.intention_analysis
        if item.contaminated is not None:
            song.contaminated = item.contaminated
        if item.contamination_note is not None:
            song.contamination_note = item.contamination_note
        song.calibrated = True
        updated.append(song)

    db.commit()
    for s in updated:
        db.refresh(s)

    return CalibrateResult(
        calibrated=len(updated),
        songs=updated,
    )


@router.delete("/songs/{song_id}", dependencies=[Depends(verify_admin_key)])
def delete_song(song_id: int, db: Session = Depends(get_db)):
    """Delete a song from the CompassSong table."""
    song = db.query(CompassSong).filter(CompassSong.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail=f"Song ID {song_id} not found")
    title, artist = song.title, song.artist
    db.delete(song)
    db.commit()
    return {"deleted": song_id, "title": title, "artist": artist}
