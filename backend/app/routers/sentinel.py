"""Sentinel Auditor Team -- public API (ships DARK).

A mission-driven red-team program: people who care whether the readings are right
apply, dig through the platform, and file findings against RC's results/algorithm.
Apply + admin approve. NOT gamified -- no score, no rank, no leaderboard; the
reward is a Compass that holds up. Findings + /me are gated by the fail-closed
flag `sentinel_auditor.enabled` (503 while dark); the waitlist + /config stay open
so people can register interest while intake is closed. The admin triage side
(routers/sentinel_admin.py) is NOT gated by the flag, so Chad can configure and
review while the public side is dark.

Mounted BARE in main.py (no X-Api-Key dep) -- it self-authenticates via Clerk
(require_clerk_user), exactly like users.router / comments.router. Posting
requires a claimed handle (the misread.py pattern). Bot protection + the shared
slowapi limiter are reused from the single-song calibrate path, as
audience_resonance does.
"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.auth import require_clerk_user
from app.models import SentinelAuditor, SentinelFinding, SentinelWaitlist, Song, SongSlug, User
from app.services import sentinel
from app.services.feature_flags import is_sentinel_auditor_enabled
from app.routers.analyzer import limiter, _check_bot_protection

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sentinel", tags=["sentinel"])


# ---------- helpers ----------

def _require_live(db: Session) -> None:
    """Fail-closed gate. Public Sentinel endpoints 503 until the flag flips."""
    if not is_sentinel_auditor_enabled(db):
        raise HTTPException(status_code=503, detail="sentinel_not_live")


def _iso(dt):
    return dt.isoformat() if dt else None


def _finding_row(f: SentinelFinding, song: Song | None, slug: str | None) -> dict:
    """The auditor's own view of a finding (status + admin disposition)."""
    return {
        "id": f.id,
        "scope": f.scope,
        "category": f.category,
        "title": f.title,
        "description": f.description,
        "evidence_url": f.evidence_url,
        "proposed_severity": f.proposed_severity,
        "accepted_severity": f.accepted_severity,
        "status": f.status,
        "disposition": f.disposition,
        "points_awarded": f.points_awarded,
        "created_at": _iso(f.created_at),
        "reviewed_at": _iso(f.reviewed_at),
        "song_id": f.song_id,
        "song_title": song.title if song else None,
        "song_artist": song.artist if song else None,
        "song_slug": slug,
    }


# ---------- schemas ----------

class ApplyIn(BaseModel):
    motivation: str = Field(min_length=20, max_length=4000)
    focus_area: str
    hp_website: str | None = None
    turnstile_token: str | None = None


class FindingIn(BaseModel):
    scope: str  # song | general
    category: str  # algorithm | methodology | data | ux | other
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=20, max_length=8000)
    song_id: int | None = None
    evidence_url: str | None = Field(default=None, max_length=2000)
    proposed_severity: str  # low | medium | high | critical
    hp_website: str | None = None
    turnstile_token: str | None = None


# ---------- config (the only endpoint that answers while dark) ----------

@router.get("/config")
def config(db: Session = Depends(get_db)):
    """Lightweight public config. `enabled` is the dark-launch gate -- false means
    the whole program is closed. Leaks nothing else."""
    return {"enabled": is_sentinel_auditor_enabled(db)}


# ---------- notify-me waitlist (always available, even while dark) ----------

class WaitlistIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    hp_website: str | None = None
    turnstile_token: str | None = None


@router.post("/waitlist")
@limiter.limit("5/hour")
async def join_waitlist(body: WaitlistIn, request: Request):
    """Capture an email so we can let the person know when applications open.
    Intentionally NOT gated by the dark flag -- the whole point is to collect
    interest while intake is closed. Honeypot + Turnstile still apply. Single-step
    (no double opt-in), deduped by email; mirrors the LC return-online list."""
    await _check_bot_protection(body.hp_website or "", body.turnstile_token or "", request)
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(422, "Please enter a valid email address.")
    db = SessionLocal()
    try:
        existing = (db.query(SentinelWaitlist)
                    .filter(SentinelWaitlist.email == email).first())
        if existing:
            return {"status": "already_on_list",
                    "message": "You are already on the list. We will write when intake opens."}
        db.add(SentinelWaitlist(email=email))
        db.commit()
        return {"status": "joined",
                "message": "Thank you. We will write the moment applications open."}
    finally:
        db.close()


# ---------- enrollment ----------

@router.post("/apply")
@limiter.limit("5/hour")
async def apply(
    body: ApplyIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_clerk_user),
):
    """Apply to join the Sentinel Auditor Team. Idempotent per user (UNIQUE
    user_id): a re-apply returns the existing application status."""
    _require_live(db)
    if not user.handle:
        raise HTTPException(status_code=409, detail="handle_required")
    if body.focus_area not in sentinel.FOCUS_AREAS:
        raise HTTPException(status_code=400, detail="bad_focus_area")
    await _check_bot_protection(body.hp_website or "", body.turnstile_token or "", request)

    existing = sentinel.get_or_none_auditor(db, user.id)
    if existing is not None:
        return {"status": existing.status, "already_applied": True}

    row = SentinelAuditor(
        user_id=user.id,
        status="pending",
        motivation=body.motivation.strip(),
        focus_area=body.focus_area,
        handle_snapshot=user.handle,
    )
    db.add(row)
    db.commit()
    return {"status": "pending", "already_applied": False}


@router.get("/me")
def me(
    db: Session = Depends(get_db),
    user: User = Depends(require_clerk_user),
):
    """The signed-in user's auditor state: enrollment status + a plain
    contribution record (findings filed / confirmed). No score, no rank.
    `auditor_status` is None when they have never applied."""
    _require_live(db)
    aud = sentinel.get_or_none_auditor(db, user.id)
    if aud is None:
        return {
            "auditor_status": None,
            "is_auditor": False,
            "has_handle": bool(user.handle),
            "focus_area": None,
            "applied_at": None,
            "contribution": {"filed": 0, "confirmed": 0},
        }
    return {
        "auditor_status": aud.status,
        "is_auditor": aud.status == "approved",
        "has_handle": bool(user.handle),
        "focus_area": aud.focus_area,
        "applied_at": _iso(aud.applied_at),
        "contribution": sentinel.contribution(db, aud.id),
    }


# ---------- findings ----------

@router.post("/findings")
@limiter.limit("10/hour")
async def submit_finding(
    body: FindingIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_clerk_user),
):
    """File a finding. Only approved auditors may submit. scope='song' requires a
    real song_id; scope='general' ignores it."""
    _require_live(db)
    aud = sentinel.get_or_none_auditor(db, user.id)
    if aud is None or aud.status != "approved":
        raise HTTPException(status_code=403, detail="not_approved_auditor")
    await _check_bot_protection(body.hp_website or "", body.turnstile_token or "", request)

    if body.scope == "song":
        if not body.song_id:
            raise HTTPException(status_code=400, detail="song_id_required")
        if not db.query(Song.id).filter(Song.id == body.song_id).first():
            raise HTTPException(status_code=404, detail="song_not_found")

    try:
        row = sentinel.record_finding(
            db,
            auditor_id=aud.id,
            scope=body.scope,
            category=body.category,
            title=body.title,
            description=body.description,
            proposed_severity=body.proposed_severity,
            song_id=body.song_id,
            evidence_url=body.evidence_url,
        )
    except sentinel.TransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return {"id": row.id, "status": row.status}


@router.get("/findings/mine")
def my_findings(
    db: Session = Depends(get_db),
    user: User = Depends(require_clerk_user),
):
    """The caller's own findings, freshest first, with admin status + disposition
    surfaced and song title/slug hydrated."""
    _require_live(db)
    aud = sentinel.get_or_none_auditor(db, user.id)
    if aud is None:
        return {"items": []}
    rows = (db.query(SentinelFinding)
            .filter(SentinelFinding.auditor_id == aud.id)
            .order_by(SentinelFinding.created_at.desc())
            .limit(300).all())
    song_ids = [r.song_id for r in rows if r.song_id is not None]
    songs, slugs = {}, {}
    if song_ids:
        for s in db.query(Song).filter(Song.id.in_(song_ids)).all():
            songs[s.id] = s
        for sl in db.query(SongSlug).filter(SongSlug.song_id.in_(song_ids)).all():
            slugs.setdefault(sl.song_id, sl.slug)
    return {"items": [_finding_row(r, songs.get(r.song_id), slugs.get(r.song_id))
                      for r in rows]}
