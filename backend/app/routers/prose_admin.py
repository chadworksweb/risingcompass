"""Dedicated prose-regeneration endpoint.

Rewrites listener_effects_prose + societal_effects_prose for a single song, re-stamps
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
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import verify_admin_or_lyrics_key
from app.database import get_db, SessionLocal
from app.models import Song

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/prose", tags=["prose-admin"])

# The stored Psyche Facts bundle's allowed keys (mirrors calibrate_song.py's
# allowlist and services/psyche_facts.py). Unknown keys are dropped.
_PF_STRING_KEYS = ("purpose", "do_not_use_if", "directions", "onset", "duration", "warning")
_PF_ARRAY_KEYS = ("indicated_for",)


def _clean_psyche_facts(raw: dict) -> dict:
    """Allowlist + trim a supplied Psyche Facts bundle. Returns the cleaned dict
    (may be empty if nothing usable survives)."""
    pf: dict = {}
    for k, v in raw.items():
        if k in _PF_STRING_KEYS and isinstance(v, str):
            t = v.strip()
            if t:
                pf[k] = t
        elif k in _PF_ARRAY_KEYS and isinstance(v, list):
            arr = [x.strip() for x in v if isinstance(x, str) and x.strip()]
            if arr:
                pf[k] = arr
    return pf

# Legacy sources resolve to the unified Song via song_id_map; 'songs' is the
# unified id directly. New callers pass source='songs'; the legacy strings keep
# old admin/frontend callers working through the map fallback.
LEGACY_SOURCES = {"compass", "library", "submitted", "stream"}


def _resolve_song_id(db: Session, source: str, song_id: int) -> Optional[int]:
    """Map a (source, song_id) pair to the unified Song.id.

    source == 'songs' -> the id is already unified. A legacy source maps via
    song_id_map. Returns None if no mapping exists.
    """
    if source == "songs":
        return song_id
    row = db.execute(
        text(
            "SELECT new_song_id FROM song_id_map "
            "WHERE old_source = :s AND old_id = :i"
        ),
        {"s": source, "i": song_id},
    ).first()
    return row[0] if row else None


class RegenRequest(BaseModel):
    source: str
    song_id: int
    lyrics: str
    # Optional supplied prose (Claude Code is the model). When either is set,
    # generation is turned OFF and the supplied text is written as-is (after the
    # verbatim-lyric scrub). This is the terminal / dry-account path: zero
    # Anthropic. Omit both to keep the server-generation behavior.
    listener_effects_prose: Optional[str] = None
    societal_effects_prose: Optional[str] = None
    # Optional supplied ether (deadpan naming + topic slugs). Same seam: when set,
    # generation stays OFF and these are written straight onto the song row. There
    # is no other live-song ether supply path, so a recalibration's corrected
    # deadpan/topics ride here. topics are validated against the ether taxonomy.
    deadpan_line: Optional[str] = None
    topics: Optional[list[str]] = None
    # Optional supplied Psyche Facts prescription bundle (Claude Code is the model).
    # There is no other live-song psyche_facts supply path -- calibrate_song.py only
    # reaches songs inside an agent draft -- so a standalone song's bundle rides
    # here. Allowlist-cleaned to the known sibling keys (mirrors calibrate_song.py).
    psyche_facts: Optional[dict] = None
    # Optional supplied per-listen effects (slugs from the closed RC vocabulary).
    # Same seam as topics: validated + written straight onto the row, no model
    # call. This is the only live-song effects_pl supply path.
    effects_pl: Optional[list[str]] = None


class RegenResult(BaseModel):
    source: str
    song_id: int
    title: str
    artist: str
    listener_effects_prose: Optional[str]
    societal_effects_prose: Optional[str]
    societal_prose_model: Optional[str]
    prior_listener_effects_prose: Optional[str]
    prior_societal_effects_prose: Optional[str]
    listener_effects_prose_changed: bool
    societal_prose_changed: bool
    deadpan_line: Optional[str] = None
    topics: Optional[list] = None
    ether_changed: bool = False
    psyche_facts: Optional[dict] = None
    psyche_facts_changed: bool = False
    effects_pl: Optional[list] = None
    effects_pl_changed: bool = False


@router.post("/regenerate", response_model=RegenResult)
async def regenerate_prose(
    body: RegenRequest,
    _auth=Depends(verify_admin_or_lyrics_key),
):
    """Regenerate listener_effects_prose + societal_effects_prose for one song.

    Archives the previous prose to prior_* columns before overwriting.
    Re-stamps societal_prose_generated_at + societal_prose_model so the
    16:00 UTC provenance sweep anchors the new version. Old anchor stays
    on-chain.

    Requires lyrics -- they are never stored in the DB.
    """
    source = body.source
    song_id = body.song_id
    lyrics = body.lyrics.strip()

    if source != "songs" and source not in LEGACY_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown source '{source}'. Use: songs (or legacy compass, library, submitted, stream)",
        )
    if not lyrics:
        raise HTTPException(status_code=422, detail="lyrics must not be empty")

    # --- Phase 1: read song, build calibration dict, archive prose ----------
    db_read = SessionLocal()
    try:
        unified_id = _resolve_song_id(db_read, source, song_id)
        if unified_id is None:
            raise HTTPException(status_code=404, detail=f"{source} song {song_id} not found")
        song = db_read.query(Song).get(unified_id)
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
        old_listener_effects_prose = song.listener_effects_prose
        old_societal_effects_prose = song.societal_effects_prose
        old_societal_prose_generated_at = song.societal_prose_generated_at
        old_societal_prose_model = song.societal_prose_model
    finally:
        db_read.close()

    # --- Phase 2: fill the prose ---------------------------------------------
    # Supplied prose (Claude Code is the model) takes the zero-Anthropic path:
    # seed the fields and turn generation OFF, so ensure_full_calibration keeps
    # only what was supplied and never calls Anthropic. With nothing supplied it
    # falls back to server generation (the original behavior).
    ether_supplied = body.deadpan_line is not None or body.topics is not None
    if body.topics is not None:
        from app.services.ether_taxonomy import VALID_SLUGS
        bad = [t for t in body.topics if t not in VALID_SLUGS]
        if bad:
            raise HTTPException(status_code=422, detail=f"invalid ether topic slug(s): {bad}")
    # A bundle-only supply (psyche_facts / effects_pl / ether, no prose) must also
    # turn generation OFF: it writes just the supplied bundle and leaves existing
    # prose untouched, never firing an Anthropic prose regen (the zero-terminal-
    # Anthropic gate). Only a bare call with nothing supplied falls to generation.
    supplied = bool(
        body.listener_effects_prose or body.societal_effects_prose
        or ether_supplied or body.psyche_facts or body.effects_pl is not None
    )
    if supplied and (body.listener_effects_prose or body.societal_effects_prose):
        from app.services.lyric_quote_guard import strip_verbatim_quotes
        if body.listener_effects_prose:
            txt, _ = strip_verbatim_quotes(body.listener_effects_prose.strip(), lyrics)
            calibration["listener_effects_prose"] = txt
        if body.societal_effects_prose:
            txt, _ = strip_verbatim_quotes(body.societal_effects_prose.strip(), lyrics)
            calibration["societal_effects_prose"] = txt

    from app.services.agents.calibrator import ensure_full_calibration
    await ensure_full_calibration(title, artist, lyrics, calibration,
                                  allow_generation=not supplied)

    # Supplied societal prose carries no server-generation seal, so stamp a
    # terminal seal (mirrors the storage-chokepoint write-time floor) before the
    # 16:00 UTC provenance sweep anchors it.
    if supplied and calibration.get("societal_effects_prose") and not calibration.get("societal_prose_generated_at"):
        from datetime import datetime
        calibration["societal_prose_generated_at"] = datetime.utcnow()
        calibration["societal_prose_model"] = "terminal_supplied"

    new_listener_effects_prose = calibration.get("listener_effects_prose")
    new_societal_effects_prose = calibration.get("societal_effects_prose")

    # Psyche Facts bundle (Claude-Code-supplied) rides straight to the row, no
    # model call. Cleaned to the allowlist; an empty result writes nothing.
    new_psyche_facts = _clean_psyche_facts(body.psyche_facts) if body.psyche_facts else None
    pf_supplied = bool(new_psyche_facts)

    # Per-listen effects: validate against the closed vocabulary (unknown slugs
    # are dropped; canonically ordered). Supplying [] intentionally clears.
    new_effects_pl = None
    epl_supplied = body.effects_pl is not None
    if epl_supplied:
        from app.services.effects_pl_vocab import clean_effects_pl, VALID_EFFECTS_PL
        bad = [s for s in body.effects_pl if s not in VALID_EFFECTS_PL]
        if bad:
            raise HTTPException(status_code=422, detail=f"invalid effects_pl slug(s): {bad}")
        new_effects_pl = clean_effects_pl(body.effects_pl)

    if (not new_listener_effects_prose and not new_societal_effects_prose
            and not ether_supplied and not pf_supplied and not epl_supplied):
        raise HTTPException(
            status_code=502,
            detail="Nothing to write -- no prose, ether, or psyche_facts produced. Check logs.",
        )

    # --- Phase 3: write (archive old, apply new) in one transaction ----------
    db_write = SessionLocal()
    try:
        song = db_write.query(Song).get(unified_id)
        if not song:
            raise HTTPException(status_code=404, detail=f"{source} song {song_id} disappeared between reads")

        # Archive previous prose (overwrite any existing prior_ snapshot).
        song.prior_listener_effects_prose = old_listener_effects_prose
        song.prior_societal_effects_prose = old_societal_effects_prose
        song.prior_societal_prose_generated_at = old_societal_prose_generated_at
        song.prior_societal_prose_model = old_societal_prose_model

        # Write new prose + new seal in lockstep.
        if new_listener_effects_prose:
            song.listener_effects_prose = new_listener_effects_prose
        if new_societal_effects_prose:
            song.societal_effects_prose = new_societal_effects_prose
            song.societal_prose_generated_at = calibration.get("societal_prose_generated_at")
            song.societal_prose_model = calibration.get("societal_prose_model")

        # Supplied ether (corrected deadpan naming + topic slugs) rides the same
        # write. topics stored as the JSON-encoded string the column expects.
        if body.deadpan_line is not None:
            song.deadpan_line = body.deadpan_line
        if body.topics is not None:
            import json as _json
            song.topics = _json.dumps(body.topics)

        # Psyche Facts bundle stored as the JSON-encoded string the column expects
        # (same convention as topics; the badge _parse_json decodes it on read).
        if new_psyche_facts:
            import json as _jsonpf
            song.psyche_facts = _jsonpf.dumps(new_psyche_facts)

        # Per-listen effects: JSON slug list, or NULL when an empty list clears.
        if epl_supplied:
            import json as _jsonepl
            song.effects_pl = _jsonepl.dumps(new_effects_pl) if new_effects_pl else None

        db_write.commit()

        logger.info(
            "prose_regen: %s/%s %s/%s -- effects=%s societal=%s ether=%s psyche_facts=%s effects_pl=%s",
            source, song_id, title, artist,
            "ok" if new_listener_effects_prose else "skipped",
            "ok" if new_societal_effects_prose else "skipped",
            "ok" if ether_supplied else "skipped",
            "ok" if pf_supplied else "skipped",
            "ok" if epl_supplied else "skipped",
        )

        topics_out = None
        if song.topics:
            import json as _json2
            try:
                topics_out = _json2.loads(song.topics)
            except (ValueError, TypeError):
                topics_out = None

        return RegenResult(
            source=source,
            song_id=song_id,
            title=title,
            artist=artist,
            listener_effects_prose=song.listener_effects_prose,
            societal_effects_prose=song.societal_effects_prose,
            societal_prose_model=song.societal_prose_model,
            prior_listener_effects_prose=old_listener_effects_prose,
            prior_societal_effects_prose=old_societal_effects_prose,
            listener_effects_prose_changed=new_listener_effects_prose is not None,
            societal_prose_changed=new_societal_effects_prose is not None,
            deadpan_line=song.deadpan_line,
            topics=topics_out,
            ether_changed=ether_supplied,
            psyche_facts=new_psyche_facts,
            psyche_facts_changed=pf_supplied,
            effects_pl=new_effects_pl,
            effects_pl_changed=epl_supplied,
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
