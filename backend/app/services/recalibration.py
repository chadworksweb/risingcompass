"""Recalibration proposals via LEC (post-Decoupling).

RC holds NO in-process scorer. Both admin recalibration pipelines -- the satire
re-read and the rubric_update re-score -- run through LEC's /api/score and map the
result into the proposal-dict shape recalibrations.py persists. This replaced the
in-process recalibrator (the former app/services/agents/recalibrator.py), which
was deleted when satire moved to LEC's universal satire MODIFIER (Decoupling /
satire). The satire law + overlay now live in LEC (le-satire + the rc-lyric satire
skin); RC just asks for a satire re-read via the satire flag.

A LEC failure raises RuntimeError -- needs human review, never a defaulted tier
(the /start endpoint maps it to 502). These are the LAST recalibration scorers; RC
no longer imports the rubric assembly or Anthropic for recalibration.
"""

import asyncio
import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.services import lec_client

logger = logging.getLogger(__name__)

# LEC scores with the same model id as RC's configured agent_model (kept in
# lockstep for parity), so the proposal's ai_model is unchanged in meaning.
AGENT_MODEL = settings.agent_model


def recalibrate_song_satire(
    title: str,
    artist: str,
    lyrics: str,
    db: Session | None = None,
    original_color: str | None = None,
    original_charge: int | None = None,
    original_summary: str | None = None,
) -> dict:
    """Re-read a song through LEC's universal satire MODIFIER (apply the standard
    rubric first, then re-read for expose-vs-endorse). Returns the proposal dict
    recalibrations.py persists; the caller persists + admin-reviews. A LEC failure
    raises RuntimeError (needs human review, never a defaulted tier).

    Stateless on LEC: the satire overlay derives its own literal starting point, so
    the original calibration is NOT sent; original_color/original_charge are used
    only to compute satire_reading_held for the proposal. db and original_summary
    are accepted for call-site compatibility with the former in-process recalibrator.
    """
    if not lyrics or not lyrics.strip():
        raise ValueError("Lyrics are required for satire recalibration.")

    # asyncio.run is safe here: /start is a sync endpoint run in a threadpool, so
    # there is no running loop in this thread (mirrors the rubric_update path).
    calibration = asyncio.run(
        lec_client.score_via_lec(title, artist, lyrics, artifact_type="lyric", satire=True)
    )
    if calibration is None:
        logger.error("LEC satire scoring failed for '%s' by %s; needs human review",
                     title, artist)
        raise RuntimeError("LEC satire scoring failed; needs human review.")

    color = calibration.get("rubric_color")
    charge_value = calibration.get("charge_value")
    contaminated = bool(calibration.get("contaminated", False))
    return {
        "rubric_color": color,
        "charge_value": charge_value,
        "contaminated": contaminated,
        "contamination_note": calibration.get("contamination_note") if contaminated else None,
        "charge_summary": calibration.get("charge_summary", ""),
        "confidence": float(calibration.get("confidence", 0.5) or 0.5),
        "ai_rationale": calibration.get("reasoning") or "",
        "ai_model": AGENT_MODEL,
        "satire_reading_held": (
            color != original_color or charge_value != original_charge
        ) if original_color else True,
    }


def recalibrate_song_rubric_update(
    title: str,
    artist: str,
    lyrics: str,
    db: Session | None = None,
) -> dict:
    """Re-read a song against LEC's LIVE rubric (used when a rubric rule or tenet
    has changed). Plain /api/score, no lens overlay. Returns the proposal dict; a
    LEC failure raises RuntimeError. db is accepted for call-site compatibility.

    Moved verbatim from the deleted recalibrator (it was already a thin LEC call
    after Scoring-Decouple Phase 1); it just no longer shares a module with an
    in-process scorer."""
    if not lyrics or not lyrics.strip():
        raise ValueError("Lyrics are required for rubric_update recalibration.")

    calibration = asyncio.run(
        lec_client.score_via_lec(title, artist, lyrics, artifact_type="lyric")
    )
    if calibration is None:
        logger.error("LEC scoring failed for rubric_update recalibration of "
                     "'%s' by %s; needs human review", title, artist)
        raise RuntimeError(
            "LEC scoring failed for rubric_update recalibration; needs human review."
        )

    contaminated = bool(calibration.get("contaminated", False))
    return {
        "rubric_color": calibration.get("rubric_color"),
        "charge_value": calibration.get("charge_value"),
        "contaminated": contaminated,
        "contamination_note": calibration.get("contamination_note") if contaminated else None,
        "charge_summary": calibration.get("charge_summary", ""),
        "confidence": float(calibration.get("confidence", 0.5) or 0.5),
        "ai_rationale": calibration.get("reasoning") or "",
        "ai_model": AGENT_MODEL,
    }
