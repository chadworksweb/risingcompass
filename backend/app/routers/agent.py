"""Admin endpoints for the Compass Agent — trigger, list, view, approve, reject, edit drafts."""

import json
import logging
import math
import re
from datetime import date

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from sqlalchemy.orm import joinedload
from app.models import AgentDraft, AgentDraftSong, DailyReading, ReadingSong, CompassSong, PrePublishCorrection
from app.schemas import (
    DraftOut, DraftTriggerIn, DraftUpdate,
    PaginatedDrafts, DraftSummary, CompassSongFeedIn, CompassSongOut,
    SupplyLyricsIn,
    PrePublishCorrectionIn, PrePublishCorrectionOut, CorrectionApplyOut,
)
from app.auth import create_approval_token, verify_approval_token, verify_reading_cron_key, verify_admin_or_lyrics_key
from app.config import settings
from app.routers.admin import verify_admin_key
from app.services.agents.compass_agent import run_compass_agent, _store_calibration, _run_post_calibration_enrichment
from app.services.artist_linker import try_link_song
from app.services.calibration_corpus import record_and_reconcile
from app.services.agents.chart_source import fetch_top_songs
from app.services.agents.calibrator import calibrate_song_async
from app.services.agents.email_notifier import send_draft_email
from app.services.compass_calc import compute_degree
from app.services.charge_calc import degree_to_charge, degree_to_score_display
from app.services.contamination import count_contaminated, enforce_contamination_rule
from app.constants import COLOR_LABELS, COLOR_HEX, draft_display_name, is_chart_draft_type

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


def _published_reading_for_ref(draft_ref: str, db: Session) -> DailyReading | None:
    """If a draft was approved and then cleaned up, find the published reading.

    Daily drafts are deleted by _cleanup_day_drafts immediately after approval,
    so a re-click on the email link would otherwise 404. The draft_ref label
    embeds the date (e.g. daily_2026-05-14_draft), which we use to locate the
    DailyReading row that the approval produced.
    """
    m = re.search(r"(\d{4}-\d{2}-\d{2})", draft_ref)
    if not m:
        return None
    try:
        reading_date = date.fromisoformat(m.group(1))
    except ValueError:
        return None
    return db.query(DailyReading).filter(DailyReading.date == reading_date).first()


def _build_already_published_html(reading: DailyReading) -> str:
    """Page shown when an approval link is clicked after the reading was published."""
    charge_color = COLOR_HEX.get(reading.charge_level, "#999")
    charge_label = COLOR_LABELS.get(reading.charge_level, reading.charge_level)
    score = degree_to_score_display(reading.compass_degree)
    contam = reading.contamination_count if reading.contamination_count is not None else 0

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reading Already Published - {reading.date}</title>
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
  .note {{ font-size:13px; color:#888; margin-bottom:24px; }}
  .editorial {{ font-size:14px; line-height:1.6; color:#aaa; font-style:italic; padding:0 8px; }}
</style>
</head><body>
<div class="card">
  <div class="check">
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
      <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>
    </svg>
  </div>
  <h1>Reading Already Published</h1>
  <div class="date">{reading.date}</div>
  <div class="note">This reading was approved earlier. Nothing to do.</div>
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
      <div class="metric-label">Contaminated</div>
      <div class="metric-value" style="color:{'#ff3333' if contam > 0 else '#00d4aa'};">{contam}</div>
    </div>
  </div>
  <div class="editorial">{reading.editorial_summary or ''}</div>
</div>
</body></html>"""


def _cleanup_day_drafts(reading_date, draft_type, db: Session) -> int:
    """Delete drafts of a given (date, draft_type) and their songs.

    Filtered by draft_type so approving the daily reading doesn't nuke a
    same-day chart-snapshot draft (Viral 50, etc.) that's still being
    worked on.
    """
    drafts = (
        db.query(AgentDraft)
        .filter(AgentDraft.date == reading_date)
        .filter(AgentDraft.draft_type == draft_type)
        .all()
    )
    count = 0
    for draft in drafts:
        db.query(AgentDraftSong).filter(AgentDraftSong.draft_id == draft.id).delete()
        db.delete(draft)
        count += 1
    if count:
        db.commit()
        logger.info("Cleaned up %d %s draft(s) for %s", count, draft_type, reading_date)
    return count


def _build_approval_html(draft) -> str:
    """Build a styled HTML confirmation page after approving a draft."""
    charge_color = COLOR_HEX.get(draft.charge_level, "#999")
    charge_label = COLOR_LABELS.get(draft.charge_level, draft.charge_level)
    score = degree_to_score_display(draft.compass_degree)
    song_count = len(draft.songs) if draft.songs else 0
    display = draft_display_name(getattr(draft, "draft_type", None))
    is_chart = is_chart_draft_type(getattr(draft, "draft_type", None))
    headline = f"{display} Approved" if is_chart else "Reading Published"

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{headline} — {draft.date}</title>
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
  <h1>{headline}</h1>
  <div class="date">{display} · {draft.date}</div>
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


@router.post("/calibrate", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def trigger_calibration(data: DraftTriggerIn, db: Session = Depends(get_db)):
    """Trigger calibration on a list of songs.

    Creates an AgentDraft with Claude-generated calibrations.
    """
    songs_input = [s.model_dump() for s in data.songs]
    reading_date = data.date or date.today()

    draft = run_compass_agent(songs_input, reading_date=reading_date, draft_only=data.draft_only, draft_type="manual")
    return draft


def _calibrate_live_impl() -> AgentDraft:
    """Shared implementation for /calibrate-live and /cron/calibrate-live.

    Owns its own short-lived sessions for chart-pin lookup and post-run
    pin-message append. `run_compass_agent` is responsible for the heavy
    work and manages its own sessions internally.
    """
    today = date.today()

    # Chart-pin lookup — short session
    chart_pinned = False
    pinned_label = None
    songs = None
    cp_db = SessionLocal()
    try:
        existing_draft = (
            cp_db.query(AgentDraft)
            .options(joinedload(AgentDraft.songs))
            .filter(AgentDraft.date == today)
            .filter(AgentDraft.draft_type == "daily")
            .order_by(AgentDraft.id.asc())
            .first()
        )
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
            pinned_label = existing_draft.label
            logger.info("Chart pinned: reusing %d songs from %s", len(songs), pinned_label)
    finally:
        cp_db.close()

    if not chart_pinned:
        songs = fetch_top_songs(count=20)
        if not songs:
            raise HTTPException(status_code=502, detail="Failed to fetch chart data from Spotify")

    draft = run_compass_agent(songs, reading_date=today, draft_type="daily")

    if not chart_pinned:
        return draft

    # Append chart-pin notice — short session, re-fetch detached with eager songs
    upd_db = SessionLocal()
    try:
        persisted = upd_db.get(AgentDraft, draft.id)
        pin_msg = f"chart_pinned: reusing {len(songs)} songs from {pinned_label}"
        if persisted.agent_notes:
            persisted.agent_notes += f"; {pin_msg}"
        else:
            persisted.agent_notes = pin_msg
        existing_warnings = json.loads(persisted.agent_warnings) if persisted.agent_warnings else []
        existing_warnings.append(pin_msg)
        persisted.agent_warnings = json.dumps(existing_warnings)
        upd_db.commit()
        draft = (
            upd_db.query(AgentDraft)
            .options(joinedload(AgentDraft.songs))
            .filter(AgentDraft.id == draft.id)
            .one()
        )
        # cascade="all" on AgentDraft.songs expunges children automatically
        upd_db.expunge(draft)
    finally:
        upd_db.close()

    return draft


@router.post("/calibrate-live", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def calibrate_live(db: Session = Depends(get_db)):
    """Fetch today's Spotify Top 50 USA and calibrate them.

    The chart is pinned: once fetched for a given day, subsequent calls
    reuse the same song list (from the first draft) so the reading is
    stable regardless of intra-day playlist shuffling.
    """
    return _calibrate_live_impl()


@router.post("/cron/calibrate-live", response_model=DraftOut, dependencies=[Depends(verify_reading_cron_key)])
def cron_calibrate_live(db: Session = Depends(get_db)):
    """Service endpoint called by the daily cron at 08:00 UTC with X-Reading-Cron-Key.

    Same logic as /calibrate-live but service-token authed.
    """
    draft = _calibrate_live_impl()
    # Daily piggyback: check whether calibrator traffic now warrants prompt
    # caching and nudge admin once if so. Fully self-contained + error-swallowing
    # so it can never affect the reading.
    from app.services.cache_advisor import evaluate_and_notify
    evaluate_and_notify()
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
def approve_draft_confirm_page(draft_ref: str, token: str = Query(...), db: Session = Depends(get_db)):
    """Show a confirmation page instead of auto-approving.

    Email clients prefetch GET links for security scanning, which was
    causing drafts to be approved without human intent. Now the GET
    shows a confirm button that POSTs to actually approve.
    Uses HMAC tokens instead of admin key in URL.
    """
    if not verify_approval_token(draft_ref, token):
        raise HTTPException(status_code=403, detail="Invalid or expired approval link")
    try:
        draft = _resolve_draft(draft_ref, db)
    except HTTPException as e:
        if e.status_code == 404:
            reading = _published_reading_for_ref(draft_ref, db)
            if reading:
                return HTMLResponse(_build_already_published_html(reading))
        raise

    if draft.status != "pending":
        return _build_approval_html(draft)

    charge_color = COLOR_HEX.get(draft.charge_level, "#999")
    charge_label = COLOR_LABELS.get(draft.charge_level, draft.charge_level)
    score = degree_to_score_display(draft.compass_degree)
    display = draft_display_name(getattr(draft, "draft_type", None))
    is_chart = is_chart_draft_type(getattr(draft, "draft_type", None))
    headline = f"Approve {display}?" if is_chart else "Publish This Reading?"

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Confirm {display} — {draft.date}</title>
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
  <h1>{headline}</h1>
  <div class="date">{display} · {draft.date}</div>
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
  <form method="POST" action="/api/admin/agent/drafts/{draft_ref}/publish?token={token}">
    <button type="submit" class="btn">Approve &amp; Publish</button>
  </form>
  <div class="label">{draft.label}</div>
</div>
</body></html>"""


@router.post("/drafts/{draft_ref}/publish", response_class=HTMLResponse)
def publish_draft_via_form(draft_ref: str, token: str = Query(...), db: Session = Depends(get_db)):
    """POST approval from the email confirmation page (form submit with token in query)."""
    if not verify_approval_token(draft_ref, token):
        raise HTTPException(status_code=403, detail="Invalid or expired approval link")
    try:
        draft = approve_draft(draft_ref, db)
    except HTTPException as e:
        if e.status_code == 404:
            reading = _published_reading_for_ref(draft_ref, db)
            if reading:
                return HTMLResponse(_build_already_published_html(reading))
        raise
    return _build_approval_html(draft)


@router.post("/drafts/{draft_ref}/approve", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def approve_draft(draft_ref: str, db: Session = Depends(get_db)):
    """Approve a draft.

    Daily/manual drafts publish a DailyReading + ReadingSongs.
    Chart-snapshot drafts (Viral 50, etc.) only mark the draft approved —
    their user-visible payload (chart_snapshots row positions and
    compass_songs entries) is already written by the cron + supply-lyrics
    flow. The draft is just the email-and-lyrics-paste vehicle.
    """
    draft = _resolve_draft(draft_ref, db)
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail="Draft cannot be approved in its current state")

    # Block approval if any songs still need lyrics/calibration
    uncalibrated = [s for s in draft.songs if s.rubric_color is None]
    if uncalibrated:
        missing = [f"{s.title} by {s.artist}" for s in uncalibrated]
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve: {len(missing)} song(s) still need lyrics: {', '.join(missing)}",
        )

    if not is_chart_draft_type(draft.draft_type):
        # Daily-reading publish path
        existing = db.query(DailyReading).filter(DailyReading.date == draft.date).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"A reading already exists for {draft.date}",
            )

        label = f"reading_{draft.date.isoformat()}"
        reading = DailyReading(
            date=draft.date,
            label=label,
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
                compass_song_id=song.compass_song_id,
                title=song.title,
                artist=song.artist,
                position=song.position,
                chart_source=song.chart_source,
            )
            db.add(rs)

    draft.status = "approved"
    db.commit()
    db.refresh(draft)

    # Snapshot response before cleanup deletes the draft
    response = DraftOut.model_validate(draft)

    # Cleanup: only nuke drafts of the SAME type so a daily approval
    # doesn't kill a same-day Viral 50 draft and vice versa.
    _cleanup_day_drafts(draft.date, draft.draft_type, db)

    return response


@router.post("/drafts/{draft_ref}/reject", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def reject_draft(draft_ref: str, db: Session = Depends(get_db)):
    """Mark a draft as rejected."""
    draft = _resolve_draft(draft_ref, db)
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail="Draft cannot be rejected in its current state")

    draft.status = "rejected"
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/drafts/{draft_ref}/resend-email", dependencies=[Depends(verify_admin_key)])
def resend_draft_email(draft_ref: str, db: Session = Depends(get_db)):
    """Resend the notification email for an existing draft."""
    draft = _resolve_draft(draft_ref, db)

    sent = send_draft_email(draft, draft.songs, settings, db=db)
    if not sent:
        raise HTTPException(status_code=500, detail="Email notification failed")
    return {"status": "sent", "to": settings.approval_email}


@router.put("/drafts/{draft_ref}", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def update_draft(draft_ref: str, data: DraftUpdate, db: Session = Depends(get_db)):
    """Edit a draft before approving — change colors, summaries, etc."""
    draft = _resolve_draft(draft_ref, db)
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail="Draft cannot be edited in its current state")

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
            if update.compass_song_id is not None:
                existing.compass_song_id = update.compass_song_id
            tmp = enforce_contamination_rule({"rubric_color": existing.rubric_color, "contaminated": existing.contaminated, "contamination_note": existing.contamination_note})
            existing.contaminated = tmp["contaminated"]
            existing.contamination_note = tmp["contamination_note"]

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


@router.post("/drafts/{draft_ref}/songs/{song_id}/lyrics", response_model=DraftOut, dependencies=[Depends(verify_admin_or_lyrics_key)])
async def supply_lyrics(draft_ref: str, song_id: int, data: SupplyLyricsIn, db: Session = Depends(get_db)):
    """Supply lyrics for an uncalibrated song in a draft, triggering calibration.

    Connection hygiene: we hold no DB session across the multi-second
    calibrator + ether_tagger Anthropic calls. Read phase snapshots what we
    need then closes the FastAPI-injected session; calibration runs against
    no DB; writes happen in a fresh session. Editorial regen, if needed,
    opens its own session.
    """
    from app.database import SessionLocal

    # === READ + SNAPSHOT — close the session before the long API call ===
    draft = _resolve_draft(draft_ref, db)
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail="Draft is not pending")
    draft_song = next((s for s in draft.songs if s.id == song_id), None)
    if not draft_song:
        raise HTTPException(status_code=404, detail=f"Song ID {song_id} not found in draft {draft_ref}")
    snap = {
        "song_id": draft_song.id,
        "title": draft_song.title,
        "artist": draft_song.artist,
        "position": draft_song.position,
        "chart_source": draft_song.chart_source or "spotify",
    }
    db.close()

    # === CALIBRATE — no DB held ===
    # Two modes:
    #   1. Terminal mode (Claude Code as model): data.calibration is supplied,
    #      so no Anthropic call. Server skips calibrator, enrichment, and
    #      editorial regen — those are also Anthropic paths. See
    #      feedback_rc_no_api_in_terminal: terminal work must not draw from
    #      the public-traffic ANTHROPIC_API_KEY budget.
    #   2. Browser/admin mode: no calibration supplied. Falls through to
    #      calibrate_song_async (Anthropic) and the full enrichment chain.
    terminal_mode = data.calibration is not None
    if terminal_mode:
        result = data.calibration.model_dump()
    else:
        result = await calibrate_song_async(
            snap["title"], snap["artist"],
            lyrics=data.lyrics, db=None, skip_cache=True,
        )

    # === WRITE PHASE — fresh session ===
    editorial_input = None
    write_db = SessionLocal()
    try:
        # Terminal-mode effects_prose / societal_effects_prose travel inside
        # `result` and are written by _store_calibration, which gates the
        # Anthropic prose-gen hook inside record_and_reconcile.
        cs_id = _store_calibration(
            snap["title"], snap["artist"], snap["position"],
            snap["chart_source"], result, True, write_db,
            lyrics=data.lyrics,
        )

        draft = _resolve_draft(draft_ref, write_db)
        ds = next(s for s in draft.songs if s.id == snap["song_id"])
        ds.rubric_color = result["rubric_color"]
        ds.charge_value = result.get("charge_value")
        ds.contaminated = result["contaminated"]
        ds.contamination_note = result["contamination_note"]
        ds.dogma_referenced = bool(result.get("dogma_referenced", False))
        ds.dogma_note = result.get("dogma_note")
        ds.charge_summary = result["charge_summary"]
        ds.confidence = result.get("confidence")
        ds.lyrics_available = True
        ds.compass_song_id = cs_id

        all_calibrated = all(s.rubric_color is not None for s in draft.songs)
        if all_calibrated:
            song_dicts = [
                {"rubric_color": s.rubric_color, "charge_value": s.charge_value, "position": s.position}
                for s in draft.songs
            ]
            draft.compass_degree = compute_degree(song_dicts)
            draft.charge_level = degree_to_charge(draft.compass_degree)
            draft.contamination_count = count_contaminated(
                [{"contaminated": s.contaminated} for s in draft.songs]
            )
            editorial_input = [
                {
                    "title": s.title, "artist": s.artist, "position": s.position,
                    "rubric_color": s.rubric_color, "charge_value": s.charge_value,
                    "contaminated": s.contaminated, "contamination_note": s.contamination_note,
                    "charge_summary": s.charge_summary, "confidence": s.confidence,
                    "lyrics_available": s.lyrics_available, "chart_source": s.chart_source,
                }
                for s in draft.songs
            ]

        write_db.commit()
    finally:
        write_db.close()

    # === ENRICHMENT — ether tagger + societal effects, each in its own short
    # session with the Anthropic call held outside any session. Must run
    # after the compass_songs row is committed (above) so the read snapshots
    # see the freshly-written row. Fails soft per step.
    # Skipped in terminal mode — those are Anthropic paths. Backfill scripts
    # or a later admin pass fill the columns in. ===
    if cs_id is not None and not terminal_mode:
        _run_post_calibration_enrichment(cs_id, data.lyrics)

    # === EDITORIAL REGEN — separate session, no DB held during API call.
    # Also an Anthropic path: skipped in terminal mode. Use PUT
    # /drafts/{ref} with editorial_summary to set it from terminal. ===
    if editorial_input and not terminal_mode:
        try:
            from app.services.agents.compass_agent import _generate_editorial
            editorial = _generate_editorial(editorial_input)
            if editorial:
                ed_db = SessionLocal()
                try:
                    ed_draft = _resolve_draft(draft_ref, ed_db)
                    ed_draft.editorial_summary = editorial
                    ed_db.commit()
                finally:
                    ed_db.close()
        except Exception:
            logger.exception("Editorial regen failed for draft %s", draft_ref)

    # === RESPONSE — fresh session, eager-load, detach, return ===
    resp_db = SessionLocal()
    try:
        out = _resolve_draft(draft_ref, resp_db)
        _ = list(out.songs)
        resp_db.expunge_all()
        return out
    finally:
        resp_db.close()


@router.post("/drafts/{draft_ref}/songs/{song_id}/correct", response_model=CorrectionApplyOut, dependencies=[Depends(verify_admin_or_lyrics_key)])
def correct_draft_song(draft_ref: str, song_id: int, data: PrePublishCorrectionIn, db: Session = Depends(get_db)):
    """Admin override of an agent-classified draft song, before draft approval.

    Replaces the direct-SQL UPDATE pattern that used to live in the
    daily-reading SOP. Atomically:
      1. Snapshots current draft_song values as before_*.
      2. Writes supplied fields to agent_draft_songs AND compass_songs.
      3. Writes one row to pre_publish_corrections capturing the diff.

    The audit row lands with promoted_to_feed=false. Promotion to the public
    Calibration Log feed is a separate deliberate step (see calibration_log
    router). human_rationale is optional here — it can be added at promote
    time instead.

    Recalculates draft metrics after the edit, matching the behavior of
    update_draft when song fields change.
    """
    from app.services.contamination import enforce_contamination_rule

    draft = _resolve_draft(draft_ref, db)
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail="Draft is not pending")

    draft_song = db.query(AgentDraftSong).filter(
        AgentDraftSong.id == song_id,
        AgentDraftSong.draft_id == draft.id,
    ).first()
    if not draft_song:
        raise HTTPException(status_code=404, detail=f"Song ID {song_id} not found in draft {draft_ref}")

    # 1. Snapshot before_*.
    before = {
        "rubric_color": draft_song.rubric_color,
        "charge_value": draft_song.charge_value,
        "contaminated": bool(draft_song.contaminated) if draft_song.contaminated is not None else None,
        "contamination_note": draft_song.contamination_note,
        "charge_summary": draft_song.charge_summary,
    }

    # 2. Apply supplied fields to draft_song.
    if data.rubric_color is not None:
        draft_song.rubric_color = data.rubric_color
    if data.charge_value is not None:
        draft_song.charge_value = data.charge_value
    if data.contaminated is not None:
        draft_song.contaminated = data.contaminated
    if data.contamination_note is not None:
        draft_song.contamination_note = data.contamination_note
    if data.charge_summary is not None:
        draft_song.charge_summary = data.charge_summary

    # Enforce contamination-rule invariants (matches update_draft).
    tmp = enforce_contamination_rule({
        "rubric_color": draft_song.rubric_color,
        "contaminated": draft_song.contaminated,
        "contamination_note": draft_song.contamination_note,
    })
    draft_song.contaminated = tmp["contaminated"]
    draft_song.contamination_note = tmp["contamination_note"]

    # Mirror to compass_songs if this draft song is linked to one.
    compass_song_id = draft_song.compass_song_id
    if compass_song_id:
        cs = db.query(CompassSong).filter(CompassSong.id == compass_song_id).first()
        if cs:
            if data.rubric_color is not None:
                cs.rubric_color = data.rubric_color
            if data.charge_value is not None:
                cs.charge_value = data.charge_value
            if data.contamination_note is not None:
                cs.contamination_note = data.contamination_note
            if data.charge_summary is not None:
                cs.charge_summary = data.charge_summary
            # Contamination flag follows the enforced draft_song value.
            cs.contaminated = draft_song.contaminated
            cs.contamination_note = draft_song.contamination_note

    # Snapshot after_* (post-enforcement).
    after = {
        "rubric_color": draft_song.rubric_color,
        "charge_value": draft_song.charge_value,
        "contaminated": bool(draft_song.contaminated) if draft_song.contaminated is not None else None,
        "contamination_note": draft_song.contamination_note,
        "charge_summary": draft_song.charge_summary,
    }

    # 3. Write audit row.
    correction = PrePublishCorrection(
        draft_id=draft.id,
        draft_song_id=draft_song.id,
        compass_song_id=compass_song_id,
        before_rubric_color=before["rubric_color"],
        before_charge_value=before["charge_value"],
        before_contaminated=before["contaminated"],
        before_contamination_note=before["contamination_note"],
        before_summary=before["charge_summary"],
        after_rubric_color=after["rubric_color"],
        after_charge_value=after["charge_value"],
        after_contaminated=after["contaminated"],
        after_contamination_note=after["contamination_note"],
        after_summary=after["charge_summary"],
        human_rationale=data.human_rationale,
        tags=data.tags,
    )
    db.add(correction)

    # Recalculate draft metrics if all songs classified (matches update_draft behavior).
    all_classified = all(s.rubric_color is not None for s in draft.songs)
    if all_classified:
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
    db.refresh(correction)

    return CorrectionApplyOut(
        draft=DraftOut.model_validate(draft),
        correction=PrePublishCorrectionOut.model_validate(correction),
    )


@router.post("/songs", response_model=CompassSongOut, dependencies=[Depends(verify_admin_key)])
def feed_song(data: CompassSongFeedIn, db: Session = Depends(get_db)):
    """Manually feed a song calibration into the CompassSong table.

    This serves two purposes:
    1. Training data for the agent (few-shot examples)
    2. Source for the public library

    All songs are stored regardless of tier — the library is non-opinionated.
    """
    current_year = data.year or date.today().year
    decade = f"{(current_year // 10) * 10}s"

    from app.services.calibration_corpus import get_or_create_song
    song, created = get_or_create_song(
        db, CompassSong,
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
        chart_source=data.chart_source,
    )
    if not created:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A compass song for '{song.title}' by '{song.artist}' already "
                f"exists (id={song.id}, year={song.year}). Use the admin DB "
                f"Explorer to edit or reset it instead of feeding a duplicate."
            ),
        )
    db.commit()
    db.refresh(song)
    try_link_song(song.title, song.artist, "compass", song.id, db)
    try:
        record_and_reconcile(
            db,
            title=song.title,
            artist=song.artist,
            calibration={
                "rubric_color": song.rubric_color,
                "charge_value": song.charge_value,
                "charge_summary": song.charge_summary,
                "contaminated": bool(song.contaminated),
                "contamination_note": song.contamination_note,
                "dogma_referenced": bool(song.dogma_referenced or False),
                "dogma_note": song.dogma_note,
                "confidence": None,
            },
            triggered_by="compass_manual",
            direct_song_source="compass",
            direct_song_id=song.id,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Corpus log failed for manual compass song %d", song.id)
    return song



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
