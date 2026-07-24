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
from sqlalchemy.orm import joinedload, selectinload
from app.models import AgentDraft, AgentDraftSong, ChartSnapshot, DailyReading, ReadingSong, PrePublishCorrection, Song, DraftSongEdit
from app.schemas import (
    DraftOut, DraftTriggerIn, DraftUpdate,
    PaginatedDrafts, DraftSummary, CompassSongFeedIn, CompassSongOut,
    SupplyLyricsIn, PreorderIn, LyricsUnavailableIn, InstrumentalIn, RecreditIn,
    PrePublishCorrectionIn, PrePublishCorrectionOut, CorrectionApplyOut,
    EditorialSupplyIn,
)
from app.auth import create_approval_token, verify_approval_token, verify_reading_cron_key, verify_admin_or_lyrics_key
from app.config import settings
from app.routers.admin import verify_admin_key
from sqlalchemy import text
from app.services.agents.compass_agent import run_compass_agent, _store_calibration, _dispatch_ether_audit
from app.services.artist_linker import parse_artist_string
from app.services.calibration_corpus import record_and_reconcile
from app.services.song_sync import store_calibrated_song
from app.services.song_identity import compute_canonical_key
from app.services.agents.chart_source import fetch_top_songs
from app.services.agents.calibrator import calibrate_song_async
from app.services.agents.email_notifier import send_draft_email
from app.services.compass_calc import compute_degree
from app.services.charge_calc import degree_to_charge, degree_to_score_display
from app.services.contamination import count_contaminated, enforce_contamination_rule
from app.constants import COLOR_LABELS, COLOR_HEX, DRAFT_TYPE_DISPLAY_NAMES, draft_display_name, is_chart_draft_type, has_null_disposition, song_needs_lyrics, chart_weighting

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


def _chart_slug_from_ref(draft_ref: str) -> str | None:
    """If draft_ref is a chart-snapshot draft label (e.g.
    itunes_download_usa_2026-06-07_draft), return its chart_source slug, else
    None. Chart slugs are the registered is_chart draft types. Longest-first so
    a longer slug can't be shadowed by a shorter prefix."""
    chart_slugs = [dt for dt in DRAFT_TYPE_DISPLAY_NAMES if is_chart_draft_type(dt)]
    for slug in sorted(chart_slugs, key=len, reverse=True):
        if draft_ref.startswith(slug + "_"):
            return slug
    return None


def _ref_date(draft_ref: str) -> date | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", draft_ref)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _published_reading_for_ref(draft_ref: str, db: Session) -> DailyReading | None:
    """If a daily/manual draft was approved and then cleaned up, find the
    published reading.

    Daily drafts are deleted by _cleanup_day_drafts immediately after approval,
    so a re-click on the email link would otherwise 404. The draft_ref label
    embeds the date (e.g. daily_2026-05-14_draft), which we use to locate the
    DailyReading row that the approval produced.

    Chart drafts also embed a date but never produce a DailyReading, so a chart
    ref must NOT resolve here (it would wrongly surface a same-date daily
    reading) -- _published_chart_for_ref handles those.
    """
    if _chart_slug_from_ref(draft_ref):
        return None
    reading_date = _ref_date(draft_ref)
    if reading_date is None:
        return None
    return db.query(DailyReading).filter(DailyReading.date == reading_date).first()


def _published_chart_for_ref(draft_ref: str, db: Session) -> tuple[str, date, int] | None:
    """Chart equivalent of _published_reading_for_ref. If a chart draft was
    approved and cleaned up, a re-click 404s on the draft; this locates the
    now-published snapshot from the ref's slug + date. Returns
    (chart_slug, date, song_count) or None."""
    slug = _chart_slug_from_ref(draft_ref)
    if not slug:
        return None
    snap_date = _ref_date(draft_ref)
    if snap_date is None:
        return None
    count = (
        db.query(ChartSnapshot)
        .filter(
            ChartSnapshot.chart_source == slug,
            ChartSnapshot.date == snap_date,
            ChartSnapshot.published.is_(True),
        )
        .count()
    )
    if not count:
        return None
    return (slug, snap_date, count)


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


def _build_chart_already_approved_html(chart_slug: str, snap_date: date, song_count: int) -> str:
    """Page shown when a chart approval link is clicked after the chart was
    approved (the draft is gone, but the snapshot is published). Chart parity
    with _build_already_published_html."""
    display = draft_display_name(chart_slug)
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{display} Already Approved - {snap_date}</title>
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
  .note {{ font-size:13px; color:#888; }}
</style>
</head><body>
<div class="card">
  <div class="check">
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
      <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>
    </svg>
  </div>
  <h1>{display} Already Approved</h1>
  <div class="date">{snap_date}</div>
  <div class="metrics">
    <div class="metric">
      <div class="metric-label">Songs</div>
      <div class="metric-value" style="color:#00d4aa;">{song_count}</div>
    </div>
  </div>
  <div class="note">This chart was approved earlier and is live. Nothing to do.</div>
</div>
</body></html>"""


def _cleanup_day_drafts(reading_date, draft_type, db: Session) -> int:
    """Delete drafts of a given (date, draft_type) and their songs.

    Filtered by draft_type so approving the daily reading doesn't nuke a
    same-day chart-snapshot draft (iTunes chart, etc.) that's still being
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
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """List agent drafts, newest first, with per-draft overview counts.

    Optional `status` filter (pending / rejected / approved) backs the Drafts
    admin queue. Songs are selectin-loaded (NOT joinedload -- a joined collection
    breaks LIMIT) so each summary carries song_count / needs_lyrics / preorder
    without an N+1 detail fetch. needs_lyrics mirrors the approve gate, so the
    queue can flag a draft "ready to approve" vs "awaiting lyrics" inline.
    """
    base = db.query(AgentDraft)
    if status:
        base = base.filter(AgentDraft.status == status)
    total = base.count()
    pages = math.ceil(total / per_page) if total > 0 else 1
    drafts = (
        base.options(selectinload(AgentDraft.songs))
        .order_by(AgentDraft.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    items = []
    for d in drafts:
        summ = DraftSummary.model_validate(d)
        songs = d.songs or []
        summ.song_count = len(songs)
        summ.needs_lyrics = sum(1 for s in songs if song_needs_lyrics(s))
        summ.preorder_count = sum(1 for s in songs if s.preorder)
        summ.display_name = draft_display_name(d.draft_type)
        summ.is_chart = is_chart_draft_type(d.draft_type)
        items.append(summ)
    return PaginatedDrafts(
        items=items,
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
            chart = _published_chart_for_ref(draft_ref, db)
            if chart:
                return HTMLResponse(_build_chart_already_approved_html(*chart))
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
            chart = _published_chart_for_ref(draft_ref, db)
            if chart:
                return HTMLResponse(_build_chart_already_approved_html(*chart))
            reading = _published_reading_for_ref(draft_ref, db)
            if reading:
                return HTMLResponse(_build_already_published_html(reading))
        raise
    return _build_approval_html(draft)


@router.post("/drafts/{draft_ref}/approve", response_model=DraftOut, dependencies=[Depends(verify_admin_key)])
def approve_draft(draft_ref: str, db: Session = Depends(get_db)):
    """Approve a draft.

    Daily/manual drafts publish a DailyReading + ReadingSongs.
    Chart-snapshot drafts (iTunes chart, etc.) only mark the draft approved —
    their user-visible payload (chart_snapshots row positions and
    compass_songs entries) is already written by the cron + supply-lyrics
    flow. The draft is just the email-and-lyrics-paste vehicle.
    """
    draft = _resolve_draft(draft_ref, db)
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail="Draft cannot be approved in its current state")

    # Block approval if any songs still need lyrics/calibration. The three null
    # dispositions are exempt: pre-order (charting before release), lyrics-
    # unavailable (released, lyrics unobtainable), and instrumental (no lyrics to
    # read at all). Each carries no reading by design and must not hold the rest
    # of the chart hostage.
    uncalibrated = [s for s in draft.songs if song_needs_lyrics(s)]
    if uncalibrated:
        missing = [f"{s.title} by {s.artist}" for s in uncalibrated]
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve: {len(missing)} song(s) still need lyrics: {', '.join(missing)}",
        )

    # === EDITORIAL REGEN AT APPROVAL ===
    # The draft's editorial was generated at draft-creation time over only the
    # THEN-calibrated (cache-hit) songs, and the terminal calibration SOP
    # (calibrate_song.py) skips editorial regen on purpose -- it supplies the
    # calibration object so the server makes zero Anthropic calls
    # (feedback_rc_no_api_in_terminal). That left a reading dominated by fresh
    # releases (e.g. New Music Friday: "one calibrated song in a field of
    # twenty") stamped with a draft-creation editorial that never saw the full
    # set. Approval is a browser/admin action, so the Anthropic call is allowed
    # here, and it is the ONE chokepoint every reading SOP (daily + every chart)
    # funnels through -- so the PUBLISHED editorial always reflects the final
    # calibrated set. Full calibration is guaranteed by the block above (pre-order
    # songs excluded, exactly as the lyrics-supply regen and the aggregates do).
    # Fail-soft: on any error keep the existing editorial rather than blocking the
    # approval.
    try:
        from app.services.agents.compass_agent import _generate_editorial
        scored = [s for s in draft.songs if not has_null_disposition(s)]
        editorial_input = [
            {
                "title": s.title, "artist": s.artist, "position": s.position,
                "rubric_color": s.rubric_color, "charge_value": s.charge_value,
                "contaminated": s.contaminated, "contamination_note": s.contamination_note,
                "charge_summary": s.charge_summary, "confidence": s.confidence,
                "lyrics_available": s.lyrics_available, "chart_source": s.chart_source,
            }
            for s in scored
        ]
        if editorial_input:
            regenerated = _generate_editorial(editorial_input)
            if regenerated:
                draft.editorial_summary = regenerated
    except Exception:
        logger.exception(
            "Approval-time editorial regen failed for draft %s; keeping existing editorial",
            draft_ref,
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
            if getattr(song, "preorder", False) or getattr(song, "lyrics_unavailable", False):
                continue  # no reading (pre-order / lyrics unavailable); not in the published list
            rs = ReadingSong(
                reading_id=reading.id,
                song_id=song.song_id,  # unified entity (Phase 5b native)
                title=song.title,
                artist=song.artist,
                position=song.position,
                chart_source=song.chart_source,
            )
            db.add(rs)
    else:
        # Chart-snapshot publish path (iTunes chart, etc.). Rebuild the public
        # snapshot FROM the approved draft songs (mirrors the daily path
        # building ReadingSong from draft.songs), published=True -- so any admin
        # edits to the draft are reflected and "published == approved" holds.
        # draft.draft_type IS the chart_source slug (e.g. itunes_download_usa).
        # Approval already blocked above if any song still needed lyrics, so
        # every published row is guaranteed calibrated. The provisional,
        # unpublished fetch-time rows for this (date, chart) are replaced here.
        db.query(ChartSnapshot).filter(
            ChartSnapshot.chart_source == draft.draft_type,
            ChartSnapshot.date == draft.date,
        ).delete(synchronize_session=False)
        db.flush()
        # Stamp the snapshot aggregate (computed during lyrics-supply, same as
        # the daily reading) onto every row so the chart-agnostic Calendar can
        # paint each day its spectrum color without recomputing on read.
        for song in sorted(draft.songs, key=lambda s: s.position):
            db.add(ChartSnapshot(
                date=draft.date,
                chart_source=draft.draft_type,
                position=song.position,
                title=song.title,
                artist=song.artist,
                compass_degree=draft.compass_degree,
                charge_level=draft.charge_level,
                editorial=draft.editorial_summary,
                published=True,
                preorder=bool(getattr(song, "preorder", False)),
            ))

    draft.status = "approved"
    db.commit()
    db.refresh(draft)

    # Snapshot response before cleanup deletes the draft
    response = DraftOut.model_validate(draft)

    # Cleanup: only nuke drafts of the SAME type so a daily approval
    # doesn't kill a same-day iTunes chart draft and vice versa.
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
                # Native (Phase 5b): the operator-supplied id links the draft song
                # to its unified songs row. (The field name predates the rename;
                # compass_song_id FK -> compass_songs would reject a unified id.)
                existing.song_id = update.compass_song_id
            tmp = enforce_contamination_rule({"rubric_color": existing.rubric_color, "contaminated": existing.contaminated, "contamination_note": existing.contamination_note})
            existing.contaminated = tmp["contaminated"]
            existing.contamination_note = tmp["contamination_note"]

        # Recalculate compass metrics after edits (uses charge_value when
        # available; pre-order songs are excluded from the aggregate).
        _recompute_draft_aggregate(draft)

    db.commit()
    db.refresh(draft)
    return draft


def _compose_terminal_calibration(result: dict) -> dict:
    """Compose a terminal-supplied calibration's color/charge from its v3
    components (Calibrator v3: the server owns the number on the terminal
    path too -- pure math, zero Anthropic calls). The legacy direct
    rubric_color + charge_value form passes through untouched; the schema
    validator guarantees one of the two forms is present.

    Mirrors the server calibrator's post-read handling: contamination is
    cross-derived from the axis data (the supplied flag is a cross-check),
    incoherence signals + escalation triggers are recorded on the dict so
    log_run stamps them on the run."""
    if result.get("route") is None:
        return result
    from app.services.charge_composition import (
        CompositionError, compose, evaluate_escalation, validate_components,
    )
    try:
        components = validate_components(result)
    except CompositionError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"v3 calibration components failed validation: {exc}",
        )
    composed = compose(components)
    result["rubric_color"] = composed.rubric_color
    result["charge_value"] = composed.charge
    result["governing_axis"] = composed.governing_axis
    result["gut_divergence"] = composed.gut_divergence

    signals = list(composed.signals)
    if bool(result.get("contaminated", False)) != composed.contaminated:
        signals.append("contamination_flag_mismatch")
    result["contaminated"] = composed.contaminated
    if not composed.contaminated:
        result["contamination_note"] = None

    triggers = evaluate_escalation(
        composed, components,
        translated=bool(result.get("translated", False)),
        confidence=result.get("confidence"),
        confidence_floor=settings.escalation_confidence_floor,
    )
    if triggers or signals:
        result["escalation_flags"] = {"triggers": triggers, "signals": signals}
    return result


@router.post("/drafts/{draft_ref}/editorial", response_model=DraftOut, dependencies=[Depends(verify_admin_or_lyrics_key)])
def supply_editorial(draft_ref: str, data: EditorialSupplyIn, db: Session = Depends(get_db)):
    """Set a draft's editorial summary from terminal (Claude Code) or admin.

    The editorial is always terminal-supplied: the server carries no
    editorial-generation path (the in-process rubric apparatus was removed in
    the Decoupling). Claude Code writes the editorial during the reading
    calibration session and supplies it here (lyrics-supply key), the same lane
    calibrate_song.py uses for per-song calibration. Approval no longer overwrites
    it: _generate_editorial is a None-returning stub, and the approval regen
    fail-softs (keeps the existing editorial) on a None result, so the supplied
    editorial is what publishes. Only mutates the editorial; aggregates are
    untouched.
    """
    draft = _resolve_draft(draft_ref, db)
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail="Draft is not pending")
    editorial = data.editorial_summary.strip()
    # Hard guard (mirrors the terminal per-song charge_summary lane): an editorial
    # is reader-facing, so it must name NO song titles (describe the reading's
    # charge, not its track list), use NO musical-genre words, and use NO tier
    # color names (use the tier label). Absence/verdict framing is not checked here
    # -- the editorial's job is to name the dominant charge + undercurrent.
    from app.services.agents.summary_guard import (
        SUMMARY_RULES_NUDGE,
        summary_violations,
    )
    _viol = summary_violations(
        editorial,
        titles=[s.title for s in draft.songs],
        check_absence=False,
        titles_multiword_only=False,
    )
    if _viol:
        raise HTTPException(
            status_code=400,
            detail="editorial tripped the summary guard: " + "; ".join(_viol)
            + ". " + SUMMARY_RULES_NUDGE,
        )
    draft.editorial_summary = editorial
    db.commit()
    db.refresh(draft)
    out = _resolve_draft(draft_ref, db)
    _ = list(out.songs)
    db.expunge_all()
    return out


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

    # === LYRICS QUALITY GATE — protect the official library (compass_songs) ===
    # Both modes below write compass_songs from here (terminal Claude-Code-supplied
    # calibration AND browser/admin Anthropic calibration), so this is the single
    # choke point for the chart-driven corpus. Hard-block short / fragmented /
    # gibberish lyrics, mirroring the public Lyrical Charger guard
    # (analyzer._validate_lyrics). Function-level import: analyzer is a sibling
    # router, imported here to avoid any module-load ordering coupling.
    from app.routers.analyzer import _validate_lyrics
    lyrics_error = _validate_lyrics(data.lyrics or "")
    if lyrics_error:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "lyrics_rejected",
                "reason": lyrics_error,
                "appeal_url": "/inquiry.html?topic=lyrics_rejected&source=supply_lyrics",
                "message": (
                    f"{lyrics_error} These lyrics were not added to the library. "
                    "If they are accurate and complete, appeal at "
                    "/inquiry.html?topic=lyrics_rejected"
                ),
            },
        )

    snap = {
        "song_id": draft_song.id,
        "title": draft_song.title,
        "artist": draft_song.artist,
        "position": draft_song.position,
        "chart_source": draft_song.chart_source or "spotify_top50_usa",
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
        result = _compose_terminal_calibration(data.calibration.model_dump())
    else:
        result = await calibrate_song_async(
            snap["title"], snap["artist"],
            lyrics=data.lyrics, db=None, skip_cache=True,
        )

    # === WRITE PHASE — fresh session ===
    editorial_input = None
    write_db = SessionLocal()
    try:
        # Terminal-mode listener_effects_prose / societal_effects_prose travel inside
        # `result` and are written by _store_calibration. In terminal mode we also
        # pass allow_prose_generation=False so record_and_reconcile NEVER calls
        # Anthropic to backfill missing prose -- Claude Code is the model and
        # supplies it; a forgotten file leaves the column NULL, never an API call.
        cs_id = _store_calibration(
            snap["title"], snap["artist"], snap["position"],
            snap["chart_source"], result, True, write_db,
            lyrics=data.lyrics,
            allow_prose_generation=not terminal_mode,
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
        ds.preorder = False  # a real calibration supersedes any prior preorder hold
        ds.lyrics_unavailable = False  # lyrics arrived; clear the unavailable hold
        ds.song_id = cs_id  # unified songs.id (Phase 5b native store)

        # Pre-order songs carry no reading; they count as "settled" for the
        # all-done check but are excluded from every aggregate (degree / charge /
        # editorial), exactly like the panel hides them from the math.
        scored = [s for s in draft.songs
                  if not getattr(s, "preorder", False)
                  and not getattr(s, "lyrics_unavailable", False)]
        all_calibrated = all(
            s.rubric_color is not None
            or getattr(s, "preorder", False)
            or getattr(s, "lyrics_unavailable", False)
            for s in draft.songs
        )
        if all_calibrated:
            song_dicts = [
                {"rubric_color": s.rubric_color, "charge_value": s.charge_value, "position": s.position}
                for s in scored
            ]
            draft.compass_degree = compute_degree(
                song_dicts, weighting=chart_weighting(draft.draft_type))
            draft.charge_level = degree_to_charge(draft.compass_degree)
            draft.contamination_count = count_contaminated(
                [{"contaminated": s.contaminated} for s in scored]
            )
            editorial_input = [
                {
                    "title": s.title, "artist": s.artist, "position": s.position,
                    "rubric_color": s.rubric_color, "charge_value": s.charge_value,
                    "contaminated": s.contaminated, "contamination_note": s.contamination_note,
                    "charge_summary": s.charge_summary, "confidence": s.confidence,
                    "lyrics_available": s.lyrics_available, "chart_source": s.chart_source,
                }
                for s in scored
            ]

        write_db.commit()
    finally:
        write_db.close()

    # === ETHER AUDIT NOTIFY — the calibration path already produced + stored
    # the ether tags + prose inside `result` (written by _store_calibration).
    # In browser/admin mode all that remains is the admin notification for a
    # no-taxonomy-match. Terminal mode supplies its own tags and skips the
    # Anthropic path, so nothing to notify here. ===
    if not terminal_mode:
        _dispatch_ether_audit(cs_id, snap["title"], snap["artist"], result.get("topic_audit"))

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


def _recompute_draft_aggregate(draft) -> None:
    """Recompute a draft's compass_degree / charge_level / contamination_count
    over its SCORED songs only (pre-order songs carry no reading and are
    excluded from every aggregate). Safe to call any time the scored set or the
    preorder set changes. No-op shape when nothing is scored yet."""
    scored = [s for s in draft.songs
              if not getattr(s, "preorder", False)
              and not getattr(s, "lyrics_unavailable", False)
              and not getattr(s, "instrumental", False)
              and s.rubric_color is not None]
    if not scored:
        return
    song_dicts = [
        {"rubric_color": s.rubric_color, "charge_value": s.charge_value, "position": s.position}
        for s in scored
    ]
    draft.compass_degree = compute_degree(
        song_dicts, weighting=chart_weighting(draft.draft_type))
    draft.charge_level = degree_to_charge(draft.compass_degree)
    draft.contamination_count = count_contaminated(
        [{"contaminated": s.contaminated} for s in scored]
    )


@router.post("/drafts/{draft_ref}/songs/{song_id}/preorder", response_model=DraftOut, dependencies=[Depends(verify_admin_or_lyrics_key)])
def mark_preorder(draft_ref: str, song_id: int, data: PreorderIn | None = None, db: Session = Depends(get_db)):
    """Null a draft song as PRE-ORDER (or clear the flag).

    For a single charting on pre-order with no lyrics yet: it has no reading by
    design, so this exempts it from the approval gate without inventing a tier.
    The flag is TEMPORARY -- unlike instrumental, a preorder is NOT a cache hit,
    so on each later day the song re-lists as awaiting-lyrics until real lyrics
    drop and a normal calibration supersedes it (which also clears this flag).

    Body is optional; `{"preorder": false}` clears the flag. Recomputes the
    draft aggregate over the scored songs after the change.
    """
    on = True if data is None else bool(data.preorder)
    draft = _resolve_draft(draft_ref, db)
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail="Draft is not pending")
    ds = next((s for s in draft.songs if s.id == song_id), None)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Song ID {song_id} not found in draft {draft_ref}")
    if on and ds.rubric_color is not None:
        raise HTTPException(
            status_code=400,
            detail="Song is already calibrated; clear the calibration before marking pre-order",
        )

    ds.preorder = on
    if on:
        # No reading: keep the calibration columns null + lyrics flag down.
        ds.rubric_color = None
        ds.charge_value = None
        ds.charge_summary = None
        ds.lyrics_available = False
    _recompute_draft_aggregate(draft)
    db.commit()
    db.refresh(draft)

    out = _resolve_draft(draft_ref, db)
    _ = list(out.songs)
    db.expunge_all()
    return out


@router.post("/drafts/{draft_ref}/songs/{song_id}/lyrics-unavailable", response_model=DraftOut, dependencies=[Depends(verify_admin_or_lyrics_key)])
def mark_lyrics_unavailable(draft_ref: str, song_id: int, data: LyricsUnavailableIn | None = None, db: Session = Depends(get_db)):
    """Null a draft song as LYRICS-UNAVAILABLE (or clear the flag).

    For a RELEASED song whose lyrics are genuinely unobtainable (not published on
    any source): it has no reading by design, so this exempts it from the approval
    gate without inventing a tier. UNLIKE pre-order, this is a PERMANENT cache hit
    -- it persists `lyrics_unavailable=True` on the unified songs row (via the
    canonical write chokepoint), so the next feeder run resolves the song as a
    cache hit and does NOT re-list it as awaiting-lyrics. A later real calibration
    (lyrics endpoint) clears the flag and supersedes the hold.

    Body is optional; `{"lyrics_unavailable": false}` clears it (and flips the
    songs-row flag back off). Recomputes the draft aggregate after the change.
    """
    on = True if data is None else bool(data.lyrics_unavailable)
    draft = _resolve_draft(draft_ref, db)
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail="Draft is not pending")
    ds = next((s for s in draft.songs if s.id == song_id), None)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Song ID {song_id} not found in draft {draft_ref}")
    if on and ds.rubric_color is not None:
        raise HTTPException(
            status_code=400,
            detail="Song is already calibrated; clear the calibration before marking lyrics-unavailable",
        )

    ds.lyrics_unavailable = on
    if on:
        # No reading: keep the calibration columns null + lyrics flag down, and
        # clear any pre-order hold (this is the stronger, permanent disposition).
        ds.rubric_color = None
        ds.charge_value = None
        ds.charge_summary = None
        ds.lyrics_available = False
        ds.preorder = False
        # Persist the disposition on the unified songs row so the feeder cache-
        # hits it and stops re-listing. only_set_present => set just this flag
        # without nulling anything else on an existing row.
        from app.services.song_sync import upsert_unified_song
        sid = upsert_unified_song(
            db, source="compass", legacy_id=None,
            row={
                "title": ds.title, "artist": ds.artist,
                "lyrics_unavailable": True, "chart_source": ds.chart_source,
            },
            ingestion_detail={"chart_source": ds.chart_source, "disposition": "lyrics_unavailable"},
            only_set_present=True,
        )
        if sid:
            ds.song_id = sid
    else:
        # Clearing: flip the songs-row flag back off so the song can re-list /
        # be calibrated normally again.
        if ds.song_id:
            db.execute(
                text("UPDATE songs SET lyrics_unavailable = FALSE WHERE id = :i"),
                {"i": ds.song_id},
            )
    _recompute_draft_aggregate(draft)
    db.commit()
    db.refresh(draft)

    out = _resolve_draft(draft_ref, db)
    _ = list(out.songs)
    db.expunge_all()
    return out


@router.post("/drafts/{draft_ref}/songs/{song_id}/recredit", response_model=DraftOut, dependencies=[Depends(verify_admin_or_lyrics_key)])
def recredit_draft_song(draft_ref: str, song_id: int, data: RecreditIn, db: Session = Depends(get_db)):
    """Correct a draft song's title and/or artist, with an audit row.

    The feeders credit whatever the platform published, which is sometimes an
    upload channel rather than a performer, and sometimes a title still carrying
    upload cruft. That pair is what mints the `songs` row and its canonical key,
    so the correction belongs HERE, before calibration: fixing it now costs a
    string, fixing it afterwards costs a song merge.

    REFUSES a song that already has a reading or a linked songs row. Once either
    exists the correction is no longer a draft-layer edit, and silently rewriting
    the credit would leave the draft disagreeing with the Library row it points
    at. Use the artist merge / song merge admin paths for those.

    Writes a `draft_song_edits` row in the SAME transaction as the change, so the
    audit can never disagree with what happened.
    """
    new_title = (data.title or "").strip() or None
    new_artist = (data.artist or "").strip() or None
    if not new_title and not new_artist:
        raise HTTPException(status_code=400, detail="Supply a title, an artist, or both")

    draft = _resolve_draft(draft_ref, db)
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail="Draft is not pending")
    ds = next((s for s in draft.songs if s.id == song_id), None)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Song ID {song_id} not found in draft {draft_ref}")
    if ds.rubric_color is not None or ds.song_id is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Song is already calibrated or linked to a Library row; recredit is a "
                "pre-calibration correction. Use the artist merge or song merge admin path."
            ),
        )

    title_before, artist_before = ds.title, ds.artist
    changed_title = bool(new_title and new_title != title_before)
    changed_artist = bool(new_artist and new_artist != artist_before)
    if not changed_title and not changed_artist:
        raise HTTPException(status_code=400, detail="Supplied values match the current credit")

    if changed_title:
        ds.title = new_title
    if changed_artist:
        ds.artist = new_artist

    db.add(DraftSongEdit(
        actor="terminal",
        draft_song_id=ds.id,
        draft_label=draft.label,
        position=ds.position,
        song_id=None,
        # Only the changed side is recorded, so the row reads as what moved.
        title_before=title_before if changed_title else None,
        title_after=ds.title if changed_title else None,
        artist_before=artist_before if changed_artist else None,
        artist_after=ds.artist if changed_artist else None,
        reason=(data.reason or "").strip() or None,
        environment=settings.environment,
    ))

    db.commit()
    db.refresh(draft)
    return draft


@router.post("/drafts/{draft_ref}/songs/{song_id}/instrumental", response_model=DraftOut, dependencies=[Depends(verify_admin_or_lyrics_key)])
def mark_instrumental(draft_ref: str, song_id: int, data: InstrumentalIn | None = None, db: Session = Depends(get_db)):
    """Null a draft song as INSTRUMENTAL (or clear the flag).

    A track with NO LYRICS TO READ. It is a PLACEHOLDER: no tier, no charge, it
    renders grey, and it stays out of every aggregate. Like lyrics_unavailable
    this is a PERMANENT cache hit -- it persists `instrumental=True` on the
    unified songs row, so the next feeder run resolves the song and does NOT
    re-list it as awaiting-lyrics. Distinct claim from its sibling: instrumental
    asserts there is nothing to read, lyrics_unavailable asserts the lyrics exist
    but cannot be obtained. A later real calibration clears the flag.

    Body is optional; `{"instrumental": false}` clears it (and flips the
    songs-row flag back off). Recomputes the draft aggregate after the change.
    """
    on = True if data is None else bool(data.instrumental)
    draft = _resolve_draft(draft_ref, db)
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail="Draft is not pending")
    ds = next((s for s in draft.songs if s.id == song_id), None)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Song ID {song_id} not found in draft {draft_ref}")
    if on and ds.rubric_color is not None:
        raise HTTPException(
            status_code=400,
            detail="Song is already calibrated; clear the calibration before marking instrumental",
        )

    ds.instrumental = on
    if on:
        # A placeholder carries no reading at all: no tier, no charge, no summary.
        ds.rubric_color = None
        ds.charge_value = None
        ds.charge_summary = None
        ds.lyrics_available = False
        ds.preorder = False
        ds.lyrics_unavailable = False
        # Persist the disposition on the unified songs row so the feeder cache-
        # hits it and stops re-listing. only_set_present => set just this flag
        # without nulling anything else on an existing row.
        from app.services.song_sync import upsert_unified_song
        sid = upsert_unified_song(
            db, source="compass", legacy_id=None,
            row={
                "title": ds.title, "artist": ds.artist,
                "instrumental": True, "chart_source": ds.chart_source,
            },
            ingestion_detail={"chart_source": ds.chart_source, "disposition": "instrumental"},
            only_set_present=True,
        )
        if sid:
            ds.song_id = sid
    else:
        # Clearing: flip the songs-row flag back off so the song can re-list /
        # be calibrated normally again.
        if ds.song_id:
            db.execute(
                text("UPDATE songs SET instrumental = FALSE WHERE id = :i"),
                {"i": ds.song_id},
            )
    _recompute_draft_aggregate(draft)
    db.commit()
    db.refresh(draft)

    out = _resolve_draft(draft_ref, db)
    _ = list(out.songs)
    db.expunge_all()
    return out


@router.post("/drafts/{draft_ref}/songs/{song_id}/correct", response_model=CorrectionApplyOut, dependencies=[Depends(verify_admin_or_lyrics_key)])
def correct_draft_song(draft_ref: str, song_id: int, data: PrePublishCorrectionIn, db: Session = Depends(get_db)):
    """Admin override of an agent-calibrated draft song, before draft approval.

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

    # Mirror to the unified songs row if this draft song is linked to one.
    unified_song_id = draft_song.song_id
    if unified_song_id:
        cs = db.query(Song).filter(Song.id == unified_song_id).first()
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
        compass_song_id=unified_song_id,  # audit pointer -> unified songs.id (Phase 5b)
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

    # Recalculate draft metrics if all songs calibrated (matches update_draft behavior).
    # Null dispositions never carry a color, so they cannot gate the recompute.
    all_calibrated = all(
        s.rubric_color is not None
        or getattr(s, "preorder", False)
        or getattr(s, "lyrics_unavailable", False)
        or getattr(s, "instrumental", False)
        for s in draft.songs
    )
    if all_calibrated:
        song_dicts = [
            {"rubric_color": s.rubric_color, "charge_value": s.charge_value, "position": s.position}
            for s in draft.songs
        ]
        draft.compass_degree = compute_degree(
            song_dicts, weighting=chart_weighting(draft.draft_type))
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
    """Manually feed a song calibration into the unified Library.

    This serves two purposes:
    1. Training data for the agent (few-shot examples)
    2. Source for the public library

    All songs are stored regardless of tier -- the library is non-opinionated.
    Native (Phase 5b): lands an atomic songs row via the storage chokepoint
    (compass / chart_reading method). A 'manual' chart_source is non-chart, so
    no chart_appearance is created; year/position are echoed in the response
    only.
    """
    current_year = data.year or date.today().year
    decade = f"{(current_year // 10) * 10}s"

    key = compute_canonical_key(data.title, data.artist)
    existing = db.execute(
        text("SELECT id, title, artist FROM songs WHERE canonical_key = :k"), {"k": key}
    ).mappings().first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A song for '{existing['title']}' by '{existing['artist']}' already "
                f"exists (id={existing['id']}). Use the admin DB Explorer to edit "
                f"or reset it instead of feeding a duplicate."
            ),
        )

    calibration = {
        "rubric_color": data.rubric_color,
        "charge_value": data.charge_value,
        "contaminated": data.contaminated,
        "contamination_note": data.contamination_note,
        "charge_summary": data.charge_summary,
    }
    song_id, _created = store_calibrated_song(
        db, source="compass",
        title=data.title, artist=data.artist, calibration=calibration,
        chart_source=data.chart_source, year=current_year, chart_position=0,
        artist_entries=parse_artist_string(data.artist or ""),
    )
    db.commit()
    try:
        record_and_reconcile(
            db,
            title=data.title,
            artist=data.artist,
            calibration={
                "rubric_color": data.rubric_color,
                "charge_value": data.charge_value,
                "charge_summary": data.charge_summary,
                "contaminated": bool(data.contaminated),
                "contamination_note": data.contamination_note,
                "dogma_referenced": False,
                "dogma_note": None,
                "confidence": None,
            },
            triggered_by="compass_manual",
            direct_song_source="songs",
            direct_song_id=song_id,
            is_new_row=True,
            # Manually-fed calibration is supplied, not server-computed; never
            # spend the public-traffic ANTHROPIC_API_KEY backfilling prose for it.
            allow_prose_generation=False,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Corpus log failed for manual compass song %s", song_id)

    s = db.execute(text(
        "SELECT id, title, artist, rubric_color, charge_value, contaminated, "
        "contamination_note, charge_summary, instrumental FROM songs WHERE id = :i"
    ), {"i": song_id}).mappings().first()
    return {
        "id": s["id"], "title": s["title"], "artist": s["artist"],
        "year": current_year, "decade": decade, "chart_position": 0,
        "rubric_color": s["rubric_color"], "charge_value": s["charge_value"],
        "contaminated": bool(s["contaminated"]),
        "contamination_note": s["contamination_note"],
        "charge_summary": s["charge_summary"],
        "instrumental": bool(s["instrumental"]),
    }


@router.delete("/songs/{song_id}", dependencies=[Depends(verify_admin_key)])
def delete_song(song_id: int, db: Session = Depends(get_db)):
    """Delete a song from the unified Library by songs.id. FK actions cascade
    its chart_appearances + ingestions and SET NULL the reading/draft/credit/
    slug references."""
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail=f"Song ID {song_id} not found")
    title, artist = song.title, song.artist
    db.delete(song)
    db.commit()
    return {"deleted": song_id, "title": title, "artist": artist}
