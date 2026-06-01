"""Dedicated prose-regeneration endpoint.

Rewrites effects_prose + societal_effects_prose for a single song, re-stamps
the provenance seal, and archives the previous prose on the row before
overwriting. The prior_* columns let you inspect what was replaced without
relying on provenance_anchor records or git history.

Provenance: a rewrite = new seal (generated_at + model) -> new hash -> the
next provenance sweep (16:00 UTC) anchors the new version. The old anchor
stays on-chain (append-only). Never raw-edit prose -- always go through this
path so the seal is always accurate to the text it seals.

Auth: admin session cookie OR RC_LYRICS_SUPPLY_KEY header (same as the
supply-lyrics endpoints). Browser admins and terminal scripts both work.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import verify_admin_or_lyrics_key
from app.database import get_db, SessionLocal
from app.models import CompassSong, LibrarySong, SubmittedSong, StreamSong

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/prose", tags=["prose-admin"])

SONG_MODELS = {
    "compass": CompassSong,
    "library": LibrarySong,
    "submitted": SubmittedSong,
    "stream": StreamSong,
}


class RegenRequest(BaseModel):
    source: str
    song_id: int
    lyrics: str


class RegenResult(BaseModel):
    source: str
    song_id: int
    title: str
    artist: str
    effects_prose: Optional[str]
    societal_effects_prose: Optional[str]
    societal_prose_model: Optional[str]
    prior_effects_prose: Optional[str]
    prior_societal_effects_prose: Optional[str]
    effects_prose_changed: bool
    societal_prose_changed: bool


@router.post("/regenerate", response_model=RegenResult)
async def regenerate_prose(
    body: RegenRequest,
    _auth=Depends(verify_admin_or_lyrics_key),
):
    """Regenerate effects_prose + societal_effects_prose for one song.

    Archives the previous prose to prior_* columns before overwriting.
    Re-stamps societal_prose_generated_at + societal_prose_model so the
    16:00 UTC provenance sweep anchors the new version. Old anchor stays
    on-chain.

    Requires lyrics -- they are never stored in the DB.
    """
    source = body.source
    song_id = body.song_id
    lyrics = body.lyrics.strip()

    model_cls = SONG_MODELS.get(source)
    if not model_cls:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown source '{source}'. Use: compass, library, submitted, stream",
        )
    if not lyrics:
        raise HTTPException(status_code=422, detail="lyrics must not be empty")

    # --- Phase 1: read song, build calibration dict, archive prose ----------
    db_read = SessionLocal()
    try:
        song = db_read.query(model_cls).filter(model_cls.id == song_id).first()
        if not song:
            raise HTTPException(status_code=404, detail=f"{source} song {song_id} not found")
        if not song.rubric_color:
            raise HTTPException(status_code=422, detail="Song has no rubric_color -- cannot generate prose")

        title = song.title
        artist = song.artist

        calibration = {
            "rubric_color": song.rubric_color,
            "charge_value": getattr(song, "charge_value", None),
            "charge_summary": getattr(song, "charge_summary", None),
            "contaminated": bool(getattr(song, "contaminated", False)),
            "contamination_note": getattr(song, "contamination_note", None),
            "deadpan_line": getattr(song, "deadpan_line", None),
            "topics": getattr(song, "topics", None),
            "topic_audit": getattr(song, "topic_audit", None),
            # Deliberately NOT carrying existing prose -- clearing them causes
            # _ensure_generation to regenerate both steps.
        }

        # Snapshot what we're about to replace.
        old_effects_prose = song.effects_prose
        old_societal_effects_prose = song.societal_effects_prose
        old_societal_prose_generated_at = song.societal_prose_generated_at
        old_societal_prose_model = song.societal_prose_model
    finally:
        db_read.close()

    # --- Phase 2: run generation (long Opus call, no DB session held) --------
    from app.services.agents.calibrator import ensure_full_calibration
    await ensure_full_calibration(title, artist, lyrics, calibration)

    new_effects_prose = calibration.get("effects_prose")
    new_societal_prose = calibration.get("societal_effects_prose")

    if not new_effects_prose and not new_societal_prose:
        raise HTTPException(
            status_code=502,
            detail="Both generation steps failed -- no prose produced. Check logs.",
        )

    # --- Phase 3: write (archive old, apply new) in one transaction ----------
    db_write = SessionLocal()
    try:
        song = db_write.query(model_cls).filter(model_cls.id == song_id).first()
        if not song:
            raise HTTPException(status_code=404, detail=f"{source} song {song_id} disappeared between reads")

        # Archive previous prose (overwrite any existing prior_ snapshot).
        song.prior_effects_prose = old_effects_prose
        song.prior_societal_effects_prose = old_societal_effects_prose
        song.prior_societal_prose_generated_at = old_societal_prose_generated_at
        song.prior_societal_prose_model = old_societal_prose_model

        # Write new prose + new seal in lockstep.
        if new_effects_prose:
            song.effects_prose = new_effects_prose
        if new_societal_prose:
            song.societal_effects_prose = new_societal_prose
            song.societal_prose_generated_at = calibration.get("societal_prose_generated_at")
            song.societal_prose_model = calibration.get("societal_prose_model")

        db_write.commit()

        logger.info(
            "prose_regen: %s/%s %s/%s -- effects=%s societal=%s",
            source, song_id, title, artist,
            "ok" if new_effects_prose else "skipped",
            "ok" if new_societal_prose else "skipped",
        )

        return RegenResult(
            source=source,
            song_id=song_id,
            title=title,
            artist=artist,
            effects_prose=song.effects_prose,
            societal_effects_prose=song.societal_effects_prose,
            societal_prose_model=song.societal_prose_model,
            prior_effects_prose=old_effects_prose,
            prior_societal_effects_prose=old_societal_effects_prose,
            effects_prose_changed=new_effects_prose is not None,
            societal_prose_changed=new_societal_prose is not None,
        )
    except HTTPException:
        db_write.rollback()
        raise
    except Exception:
        db_write.rollback()
        logger.exception("prose_regen write failed for %s/%s", source, song_id)
        raise HTTPException(status_code=500, detail="Write failed -- see logs")
    finally:
        db_write.close()
