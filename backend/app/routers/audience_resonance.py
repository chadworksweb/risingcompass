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
from app.models import Resonance, Song, SongSlug, User
from app.services import resonance_slicer
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


# ---------- slice jobs (in-memory, transient) ----------
# A slice is ONE short classification, so the start -> token -> poll -> reveal
# pattern (from the Lyrical Charger async path) runs against an in-process
# registry rather than its own DB table: the durable result lands on the
# resonances row at /submit, and a slice ships dark (neutral) until the flag is
# flipped, so cross-restart job survival is not needed for v1. If a robust,
# cross-session job ledger is wanted later, mirror album_charge_jobs (migration).
_slice_jobs: dict[str, dict] = {}
_slice_tasks: set[asyncio.Task] = set()

# A slice resolves in well under a minute; anything older is dropped on poll
# (covers a container restart -- the in-process task is gone) and pruned.
SLICE_JOB_TTL = timedelta(minutes=10)


def _prune_slice_jobs() -> None:
    """Drop expired job entries so the registry can't grow unbounded."""
    now = datetime.utcnow()
    stale = [t for t, j in _slice_jobs.items()
             if (now - j.get("created_at", now)) > SLICE_JOB_TTL]
    for t in stale:
        _slice_jobs.pop(t, None)


async def _run_slice(token: str, song_id: int, story: str) -> None:
    """Background worker: classify one testimony and store the slice on the job
    entry. Never raises into the event loop -- failures land as a neutral slice."""
    job = _slice_jobs.get(token)
    if job is None:
        return
    job["status"] = "running"
    db = SessionLocal()
    try:
        song = db.query(Song).filter(Song.id == song_id).first()
        title = song.title if song else ""
        artist = song.artist if song else ""
        slice_result = await resonance_slicer.slice_story(
            story=story, title=title, artist=artist, db=db)
        job["slice"] = slice_result
        job["status"] = "done"
    except Exception:
        logger.exception("resonance slice job %s failed", token)
        job["status"] = "done"
        # Fail-soft: a neutral slice so the wizard can still proceed to consent.
        from app.services import resonance_rubric
        job["slice"] = resonance_rubric.neutral_slice("worker_error")
    finally:
        db.close()


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


# ---------- reads (public) ----------

def _published(query):
    """Only published, non-synthetic, non-contested rows are ever public. A row
    in flagged / in_review is held back until a human upholds or corrects it
    (SCOPE: a flagged verdict routes to review before anything goes public);
    none / upheld / corrected are the publishable states."""
    return query.filter(
        Resonance.consent_tier == "publish",
        Resonance.is_synthetic.is_(False),
        Resonance.flag_state.notin_(("flagged", "in_review")),
    )


@router.get("/song/{song_id}")
def song_resonances(song_id: int, db: Session = Depends(get_db)):
    rows = _published(db.query(Resonance).filter(Resonance.song_id == song_id)).all()
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
    }


@router.get("/corpus")
def corpus(db: Session = Depends(get_db)):
    rows = _published(db.query(Resonance)).all()
    agg = {}
    for r in rows:
        a = agg.setdefault(r.song_id, {"n": 0, "t": 0, "c": 0, "a": 0})
        a["n"] += 1
        a["t"] += r.prop_true
        a["c"] += r.prop_camouflage
        a["a"] += r.prop_adjacent
    if not agg:
        return {"songs": []}
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
    return {"songs": out}


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
    _slice_jobs[token] = {
        "status": "queued", "slice": None,
        "created_at": datetime.utcnow(),
    }
    task = asyncio.create_task(_run_slice(token, body.song_id, body.story.strip()))
    _slice_tasks.add(task)
    task.add_done_callback(_slice_tasks.discard)
    return {"slice_token": token, "status": "queued"}


@router.get("/slice/{token}")
@limiter.limit("600/hour")
async def slice_status(token: str, request: Request):
    """Poll a slice. Returns the proportional verdict + line-by-line attribution
    once done. Reveal-on-commit: the frontend shows this, then the person accepts
    or flags, then chooses consent and calls /submit with this same token."""
    job = _slice_jobs.get(token)
    if job is None:
        # Unknown or expired (e.g. a restart wiped the registry).
        raise HTTPException(status_code=404, detail="unknown_slice")
    if job["status"] in ("queued", "running") \
            and (datetime.utcnow() - job["created_at"]) > SLICE_JOB_TTL:
        return {"status": "error", "error": "slice_timed_out"}
    return {"status": job["status"], "slice": job.get("slice")}


# ---------- writes ----------

@router.post("/submit")
def submit(body: SubmitIn, db: Session = Depends(get_db),
           current_user: User | None = Depends(optional_clerk_user)):
    song = db.query(Song).filter(Song.id == body.song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="song_not_found")
    consent = body.consent if body.consent in ("publish", "private") else "private"

    # Persist the SERVER-computed slice referenced by slice_token (proportions
    # are never trusted from the client). No / unresolved token -> neutral
    # (which is also the steady state while the slicer ships dark).
    prop_true = prop_camouflage = prop_adjacent = 0
    slice_attribution = None
    job = _slice_jobs.get(body.slice_token) if body.slice_token else None
    sliced = job.get("slice") if job else None
    if sliced and sliced.get("status") == "done":
        prop_true = sliced.get("prop_true", 0)
        prop_camouflage = sliced.get("prop_camouflage", 0)
        prop_adjacent = sliced.get("prop_adjacent", 0)
        if sliced.get("slice_attribution"):
            slice_attribution = json.dumps(sliced["slice_attribution"])

    # The "did we misread your story" lever, set at the reveal step, routes the
    # row to human review before it can publish (distinct from the song satire flag).
    flag_state = "flagged" if body.flagged else "none"

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
        is_synthetic=False,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    # Slice consumed -> drop it from the registry.
    if body.slice_token:
        _slice_jobs.pop(body.slice_token, None)
    return {"id": row.id, "status": "received", "consent": consent, "flag": flag_state}


@router.post("/{resonance_id}/flag")
def flag(resonance_id: int, db: Session = Depends(get_db),
         current_user: User | None = Depends(optional_clerk_user)):
    row = db.query(Resonance).filter(Resonance.id == resonance_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    # "Did we misread your story" -> routes to human review before publish.
    row.flag_state = "flagged"
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
