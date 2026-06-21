"""Audience Resonance -- public API for RC's fourth instrument.

Listener testimony about a song, sliced into a proportional verdict
(True / Camouflage / Adjacent). Serves the READS that power the song-page
section and the standalone corpus map, plus submission, the "did we misread
your story" flag, and a hard-delete (the user's true-erasure escape hatch).

Reads are public (the router is registered with the public-read dep in main.py).
Submission / flag / delete resolve the optional signed-in user for attribution.

The SLICER (the classifier that produces the proportional verdict) runs as an
async job: POST /slice -> token -> GET /slice/{token} poll -> reveal, then POST
/submit references that token so the SERVER-computed slice is persisted (the
client never supplies proportions). The slicer SHIPS DARK behind the fail-closed
flag `resonance_slicer.enabled` -- while off, the worker resolves to a neutral
verdict (status 'pending') and makes NO model call (see services/resonance_slicer.py;
the model invocation is a single unwired seam). Nothing in this module calls
Anthropic. Synthetic seed rows (is_synthetic) and contested rows (flagged /
in_review) are excluded from every public read.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.auth import optional_clerk_user
from app.models import Resonance, ResonanceSliceJob, Song, SongSlug, User
from app.services import resonance_slicer
from app.services.feature_flags import is_audience_resonance_enabled
# Reuse the single-song calibrate path's bot protection + app-registered limiter,
# exactly as album_charger does -- one source of truth for both.
from app.routers.analyzer import limiter, _check_bot_protection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audience-resonance", tags=["audience-resonance"])

# Mirror of the site-wide charge-tier palette (frontend COLOR_HEX), inlined to
# keep this isolated feature from importing chart internals.
COLOR_HEX = {
    "violet": "#aa54ff", "blue": "#3388ff", "green": "#33cc55",
    "orange": "#ffbb33", "red": "#ff3333",
}
TIER_LABEL = {
    "violet": "Ascended", "blue": "Elevated", "green": "Decent",
    "orange": "Degraded", "red": "Corrupted",
}


def _color(tier):
    return COLOR_HEX.get(tier or "", "#888888")


# ---------- slice jobs (durable, DB-backed) ----------
# The start -> token -> poll -> reveal flow is backed by resonance_slice_jobs so
# the job + computed slice survive a worker restart and resolve across multiple
# uvicorn workers (a token minted on one worker must be readable by another at
# /submit). Mirrors album_charge_jobs. Module set holds in-flight asyncio tasks
# so the loop doesn't GC them.
_slice_tasks: set[asyncio.Task] = set()

# A slice resolves in well under a minute; a job stuck past this (e.g. a
# container restart killed the worker) is reported errored on poll.
SLICE_JOB_TTL = timedelta(minutes=10)


def _update_slice_job(token: str, **fields) -> None:
    """Short-lived write to the job row. Bumps updated_at; swallows errors so
    slice telemetry can never crash the worker."""
    fields["updated_at"] = datetime.utcnow()
    db = SessionLocal()
    try:
        db.query(ResonanceSliceJob).filter(
            ResonanceSliceJob.job_token == token).update(fields)
        db.commit()
    except Exception:
        logger.exception("resonance slice job %s update failed", token)
    finally:
        db.close()


def _prune_slice_jobs() -> None:
    """Delete job rows older than the TTL so the table stays bounded (the slice
    result a submission needs is copied onto the resonances row at /submit)."""
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - SLICE_JOB_TTL
        db.query(ResonanceSliceJob).filter(
            ResonanceSliceJob.created_at < cutoff).delete(synchronize_session=False)
        db.commit()
    except Exception:
        logger.exception("resonance slice job prune failed")
    finally:
        db.close()


async def _run_slice(token: str, song_id: int, story: str) -> None:
    """Background worker: classify one testimony and store the slice on the job
    row. Never raises into the event loop -- failures land as a neutral slice."""
    _update_slice_job(token, status="running")
    db = SessionLocal()
    try:
        song = db.query(Song).filter(Song.id == song_id).first()
        title = song.title if song else ""
        artist = song.artist if song else ""
        slice_result = await resonance_slicer.slice_story(
            story=story, title=title, artist=artist, db=db)
    except Exception:
        logger.exception("resonance slice job %s failed", token)
        from app.services import resonance_rubric
        slice_result = resonance_rubric.neutral_slice("worker_error")
    finally:
        db.close()
    _update_slice_job(token, status="done", slice_json=json.dumps(slice_result))


# ---------- schemas ----------

class ResonanceCard(BaseModel):
    id: int
    username: str
    story: str
    true: int
    camouflage: int
    adjacent: int
    flag: str


class SongRollup(BaseModel):
    song_id: int
    title: str
    artist: str
    slug: str | None
    tier: str | None
    tier_label: str | None
    charge: int | None
    color: str
    n: int
    mean_true: float
    mean_camouflage: float
    mean_adjacent: float


class SliceIn(BaseModel):
    song_id: int
    story: str = Field(min_length=20, max_length=8000)
    # Bot protection (mirrors the LC calibrate path); both optional so the dark
    # build works without Turnstile wired on the new wizard yet.
    hp_website: str | None = None
    turnstile_token: str | None = None


class SubmitIn(BaseModel):
    song_id: int
    username: str = Field(min_length=1, max_length=120)
    story: str = Field(min_length=20, max_length=8000)
    consent: str = "private"  # publish | private
    # Reveal-on-commit: the token from POST /slice. When present and resolved,
    # the SERVER-computed slice is persisted (proportions are never trusted from
    # the client). Absent / unresolved -> a neutral verdict is stored.
    slice_token: str | None = None
    # The "did we misread your story" lever, set at the reveal step.
    flagged: bool = False
    # Short note on what the slicer got wrong (stored when flagged).
    flag_reason: str | None = Field(default=None, max_length=2000)


class FlagIn(BaseModel):
    # Optional note when flagging an already-posted resonance from the display.
    reason: str | None = Field(default=None, max_length=2000)


# ---------- reads (public) ----------

def _visible(query, db):
    """Flag-aware public read.

    LIVE (audience_resonance.enabled): real, published, resolved rows only. A row
    in flagged / in_review is held back until a human upholds or corrects it
    (SCOPE: a flagged verdict routes to review before anything goes public).
    DARK (default): serve the curated is_synthetic DEMO set instead, so the
    surfaces are populated for the dark launch (the frontend watermarks it).
    Real submissions collected while dark stay hidden until the flag flips."""
    if is_audience_resonance_enabled(db):
        return query.filter(
            Resonance.consent_tier == "publish",
            Resonance.is_synthetic.is_(False),
            Resonance.flag_state.notin_(("flagged", "in_review")),
        )
    return query.filter(
        Resonance.consent_tier == "publish",
        Resonance.is_synthetic.is_(True),
    )


@router.get("/song/{song_id}")
def song_resonances(song_id: int, db: Session = Depends(get_db)):
    rows = _visible(db.query(Resonance).filter(Resonance.song_id == song_id), db).all()
    n = len(rows)
    mt = sum(r.prop_true for r in rows) / n if n else 0.0
    mc = sum(r.prop_camouflage for r in rows) / n if n else 0.0
    ma = sum(r.prop_adjacent for r in rows) / n if n else 0.0
    cards = [
        ResonanceCard(
            id=r.id, username=r.username, story=r.story_text,
            true=r.prop_true, camouflage=r.prop_camouflage, adjacent=r.prop_adjacent,
            flag=r.flag_state,
        )
        for r in rows
    ]
    return {
        "song_id": song_id,
        "count": n,
        "mean": {"true": mt, "camouflage": mc, "adjacent": ma},
        "resonances": cards,
        "demo": not is_audience_resonance_enabled(db),
    }


@router.get("/corpus")
def corpus(db: Session = Depends(get_db)):
    demo = not is_audience_resonance_enabled(db)
    rows = _visible(db.query(Resonance), db).all()
    agg = {}
    for r in rows:
        a = agg.setdefault(r.song_id, {"n": 0, "t": 0, "c": 0, "a": 0})
        a["n"] += 1
        a["t"] += r.prop_true
        a["c"] += r.prop_camouflage
        a["a"] += r.prop_adjacent
    if not agg:
        return {"songs": [], "demo": demo}
    ids = list(agg.keys())
    songs = {s.id: s for s in db.query(Song).filter(Song.id.in_(ids)).all()}
    # Slug per song for dot click-through (/songs/{slug}). First slug wins.
    slug_by_song: dict[int, str] = {}
    for row in db.query(SongSlug).filter(SongSlug.song_id.in_(ids)).all():
        slug_by_song.setdefault(row.song_id, row.slug)
    out = []
    for sid, a in agg.items():
        s = songs.get(sid)
        if not s:
            continue
        n = a["n"]
        out.append(SongRollup(
            song_id=sid, title=s.title, artist=s.artist,
            slug=slug_by_song.get(sid),
            tier=s.rubric_color, tier_label=TIER_LABEL.get(s.rubric_color or ""),
            charge=s.charge_value, color=_color(s.rubric_color),
            n=n, mean_true=a["t"] / n, mean_camouflage=a["c"] / n, mean_adjacent=a["a"] / n,
        ))
    return {"songs": out, "demo": demo}


# ---------- slice (the async slicer: start -> token -> poll -> reveal) ----------

@router.post("/slice", status_code=202)
@limiter.limit("20/hour")
async def start_slice(body: SliceIn, request: Request, db: Session = Depends(get_db)):
    """Kick off the slice for one testimony BEFORE it is persisted (reveal on
    commit). Validates the song exists, bot-checks, then runs the classifier in
    the background and returns a token to poll. While the slicer ships dark the
    worker resolves immediately to a neutral verdict (status='pending')."""
    song = db.query(Song).filter(Song.id == body.song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="song_not_found")
    # Empty-string fallbacks: the shared checker calls .strip() on the honeypot
    # and treats a missing Turnstile token as a failure only when turnstile_secret
    # is configured (step 3 wires the token into the wizard).
    await _check_bot_protection(body.hp_website or "", body.turnstile_token or "", request)

    _prune_slice_jobs()
    token = uuid.uuid4().hex
    db.add(ResonanceSliceJob(job_token=token, status="queued"))
    db.commit()
    task = asyncio.create_task(_run_slice(token, body.song_id, body.story.strip()))
    _slice_tasks.add(task)
    task.add_done_callback(_slice_tasks.discard)
    return {"slice_token": token, "status": "queued"}


@router.get("/slice/{token}")
@limiter.limit("600/hour")
async def slice_status(token: str, request: Request, db: Session = Depends(get_db)):
    """Poll a slice. Returns the proportional verdict + line-by-line attribution
    once done. Reveal-on-commit: the frontend shows this, then the person accepts
    or flags, then chooses consent and calls /submit with this same token."""
    job = db.query(ResonanceSliceJob).filter(ResonanceSliceJob.job_token == token).first()
    if job is None:
        raise HTTPException(status_code=404, detail="unknown_slice")
    if job.status in ("queued", "running") and job.updated_at \
            and (datetime.utcnow() - job.updated_at) > SLICE_JOB_TTL:
        return {"status": "error", "error": "slice_timed_out"}
    sl = None
    if job.slice_json:
        try:
            sl = json.loads(job.slice_json)
        except Exception:
            logger.exception("slice job %s result parse failed", token)
    return {"status": job.status, "slice": sl}


# ---------- writes ----------

@router.post("/submit")
def submit(body: SubmitIn, db: Session = Depends(get_db),
           current_user: User | None = Depends(optional_clerk_user)):
    # Dark launch (like album_charger): submissions are closed until the feature
    # flag flips. Reveal/preview still works; only the write is gated.
    if not is_audience_resonance_enabled(db):
        raise HTTPException(status_code=503, detail="audience_resonance_not_live")
    song = db.query(Song).filter(Song.id == body.song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="song_not_found")
    consent = body.consent if body.consent in ("publish", "private") else "private"

    # Persist the SERVER-computed slice referenced by slice_token (proportions
    # are never trusted from the client). No / unresolved token -> neutral
    # (which is also the steady state while the slicer ships dark).
    prop_true = prop_camouflage = prop_adjacent = 0
    slice_attribution = None
    job = (db.query(ResonanceSliceJob)
           .filter(ResonanceSliceJob.job_token == body.slice_token).first()
           if body.slice_token else None)
    sliced = None
    if job and job.slice_json:
        try:
            sliced = json.loads(job.slice_json)
        except Exception:
            sliced = None
    if sliced and sliced.get("status") == "done":
        prop_true = sliced.get("prop_true", 0)
        prop_camouflage = sliced.get("prop_camouflage", 0)
        prop_adjacent = sliced.get("prop_adjacent", 0)
        if sliced.get("slice_attribution"):
            slice_attribution = json.dumps(sliced["slice_attribution"])

    # The "did we misread your story" lever, set at the reveal step, routes the
    # row to human review before it can publish (distinct from the song satire flag).
    flag_state = "flagged" if body.flagged else "none"
    flag_reason = (body.flag_reason or "").strip()[:2000] if body.flagged else None

    row = Resonance(
        song_id=body.song_id,
        user_id=(current_user.id if current_user else None),
        username=body.username.strip(),
        story_text=body.story.strip(),
        prop_true=prop_true,
        prop_camouflage=prop_camouflage,
        prop_adjacent=prop_adjacent,
        slice_attribution=slice_attribution,
        consent_tier=consent,
        flag_state=flag_state,
        flag_reason=flag_reason,
        is_synthetic=False,
    )
    db.add(row)
    # Slice consumed -> drop the job row in the same transaction.
    if job is not None:
        db.delete(job)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "status": "received", "consent": consent, "flag": flag_state}


@router.post("/{resonance_id}/flag")
def flag(resonance_id: int, body: FlagIn | None = None, db: Session = Depends(get_db),
         current_user: User | None = Depends(optional_clerk_user)):
    row = db.query(Resonance).filter(Resonance.id == resonance_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    # "Did we misread your story" -> routes to human review before publish.
    row.flag_state = "flagged"
    if body and body.reason:
        row.flag_reason = body.reason.strip()[:2000]
    db.commit()
    return {"id": resonance_id, "flag": "flagged"}


@router.delete("/{resonance_id}")
def delete(resonance_id: int, db: Session = Depends(get_db),
           current_user: User | None = Depends(optional_clerk_user)):
    row = db.query(Resonance).filter(Resonance.id == resonance_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    # True erasure, owner-gated. Anonymous-submitted rows need a delete token
    # (follow-up); for now deletion requires the signed-in owner.
    if row.user_id is None or current_user is None or row.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="not_owner")
    db.delete(row)
    db.commit()
    return {"id": resonance_id, "status": "deleted"}
