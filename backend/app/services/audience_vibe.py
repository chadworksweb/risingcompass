"""Audience Vibe service — push application, eligibility, threshold trigger.

Two needles, one song. The compass is diagnostic; the vibe is democratic.
The gap between them IS the data. This service handles the mechanics of how
pushes move the needle and when the gap opens an admin review case.

v1 identity model is device-id-only (anonymous, gameable at small scale —
roadmap accepts that "scale is the defense" until real-account auth ships).
The schema reserves a nullable user_id column so account-based gating can
layer in without a migration.
"""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import func, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    AudienceVibeNeedle, AudienceVibePush, AudienceVibeReviewCase,
    CompassSong, LibrarySong, SubmittedSong,
)

logger = logging.getLogger(__name__)

VIBE_MIN = -100
VIBE_MAX = 100

SONG_MODELS = {
    "compass": CompassSong,
    "library": LibrarySong,
    "submitted": SubmittedSong,
}


class VibeError(Exception):
    """Domain error from the vibe service. Message is safe to surface to the caller."""


def _resolve_song(db: Session, source: str, song_id: int):
    model = SONG_MODELS.get(source)
    if not model:
        raise VibeError(f"Invalid song_source: {source}")
    row = db.query(model).get(song_id)
    if not row:
        raise VibeError(f"{source} song id={song_id} not found")
    return row


def _initial_needle_value(db: Session, source: str, song_id: int) -> int:
    """Initial audience needle value = compass charge so the offset starts at
    0. Falls back to 0 for uncalibrated songs."""
    try:
        song = _resolve_song(db, source, song_id)
    except VibeError:
        return 0
    return int(getattr(song, "charge_value", None) or 0)


def _get_or_create_needle(db: Session, source: str, song_id: int) -> AudienceVibeNeedle:
    needle = (
        db.query(AudienceVibeNeedle)
        .filter(AudienceVibeNeedle.song_source == source)
        .filter(AudienceVibeNeedle.song_id == song_id)
        .first()
    )
    if needle:
        return needle
    needle = AudienceVibeNeedle(
        song_source=source, song_id=song_id,
        current_value=_initial_needle_value(db, source, song_id),
        pushes_up_total=0, pushes_down_total=0,
    )
    db.add(needle)
    db.flush()
    return needle


def _check_eligibility(
    db: Session, source: str, song_id: int, device_id: str, year: int,
) -> bool:
    """True if this device has not already pushed this song this year."""
    if not device_id:
        return False
    existing = (
        db.query(AudienceVibePush.id)
        .filter(AudienceVibePush.song_source == source)
        .filter(AudienceVibePush.song_id == song_id)
        .filter(AudienceVibePush.device_id == device_id)
        .filter(AudienceVibePush.push_year == year)
        .first()
    )
    return existing is None


def _year_directional_split(db: Session, source: str, song_id: int, year: int) -> dict:
    """Counts of pushes in the current year, by direction."""
    rows = (
        db.query(AudienceVibePush.direction, func.count(AudienceVibePush.id))
        .filter(AudienceVibePush.song_source == source)
        .filter(AudienceVibePush.song_id == song_id)
        .filter(AudienceVibePush.push_year == year)
        .group_by(AudienceVibePush.direction)
        .all()
    )
    counts = {1: 0, -1: 0}
    for direction, n in rows:
        counts[int(direction)] = int(n)
    return {"up": counts[1], "down": counts[-1]}


def _maybe_open_review_case(
    db: Session, source: str, song_id: int, vibe_value: int,
) -> Optional[AudienceVibeReviewCase]:
    """If the gap exceeds threshold, open a review case (or update the existing
    open one with the latest snapshot). Only one open case per song.
    """
    threshold = settings.vibe_review_threshold
    song = _resolve_song(db, source, song_id)
    compass_charge = getattr(song, "charge_value", None)
    compass_color = getattr(song, "rubric_color", None)
    if compass_charge is None:
        return None

    gap = abs(compass_charge - vibe_value)
    if gap <= threshold:
        return None

    existing = (
        db.query(AudienceVibeReviewCase)
        .filter(AudienceVibeReviewCase.song_source == source)
        .filter(AudienceVibeReviewCase.song_id == song_id)
        .filter(AudienceVibeReviewCase.status == "open")
        .first()
    )
    if existing:
        existing.compass_charge = compass_charge
        existing.compass_color = compass_color
        existing.vibe_value = vibe_value
        existing.gap = gap
        return existing

    case = AudienceVibeReviewCase(
        song_source=source, song_id=song_id,
        compass_charge=compass_charge, compass_color=compass_color,
        vibe_value=vibe_value, gap=gap, status="open",
    )
    db.add(case)
    return case


def get_state(db: Session, source: str, song_id: int, device_id: Optional[str]) -> dict:
    """Public read of the vibe state for one song."""
    _resolve_song(db, source, song_id)  # 404-fail if song missing
    needle = (
        db.query(AudienceVibeNeedle)
        .filter(AudienceVibeNeedle.song_source == source)
        .filter(AudienceVibeNeedle.song_id == song_id)
        .first()
    )
    year = datetime.utcnow().year

    if not needle:
        eligible = bool(device_id)
        return {
            # Synthesise the initial value (= compass charge) so the frontend
            # offset visualization shows 0 before any push has been recorded.
            "value": _initial_needle_value(db, source, song_id),
            "pushes_up_total": 0,
            "pushes_down_total": 0,
            "year_split": {"up": 0, "down": 0},
            "eligible_to_push": eligible,
            "current_year": year,
        }

    eligible = _check_eligibility(db, source, song_id, device_id, year) if device_id else False
    return {
        "value": needle.current_value,
        "pushes_up_total": needle.pushes_up_total,
        "pushes_down_total": needle.pushes_down_total,
        "year_split": _year_directional_split(db, source, song_id, year),
        "eligible_to_push": eligible,
        "current_year": year,
        "last_push_at": needle.last_push_at.isoformat() if needle.last_push_at else None,
    }


def apply_push(
    db: Session,
    source: str,
    song_id: int,
    direction: int,
    device_id: Optional[str],
    ip_address: Optional[str],
) -> dict:
    """Apply one push. Enforces eligibility, updates the needle, opens a review
    case if the gap crosses threshold. Returns the updated state.
    """
    if direction not in (1, -1):
        raise VibeError("direction must be +1 or -1")
    if not device_id:
        raise VibeError("device_id is required to push the vibe")

    _resolve_song(db, source, song_id)  # validate target
    year = datetime.utcnow().year

    if not _check_eligibility(db, source, song_id, device_id, year):
        raise VibeError("Already pushed this song this year. Each person gets one push per song per year.")

    needle = _get_or_create_needle(db, source, song_id)

    push = AudienceVibePush(
        song_source=source, song_id=song_id, direction=direction,
        device_id=device_id, ip_address=ip_address, push_year=year,
    )
    db.add(push)

    new_value = max(VIBE_MIN, min(VIBE_MAX, needle.current_value + direction))
    needle.current_value = new_value
    if direction == 1:
        needle.pushes_up_total += 1
    else:
        needle.pushes_down_total += 1
    needle.last_push_at = datetime.utcnow()
    needle.updated_at = needle.last_push_at

    case = _maybe_open_review_case(db, source, song_id, new_value)

    try:
        db.commit()
    except IntegrityError:
        # Race-loss on the (source, id, device, year) unique constraint —
        # someone else won the upsert. Roll back and surface as a normal
        # ineligibility error.
        db.rollback()
        raise VibeError("Already pushed this song this year. Each person gets one push per song per year.")

    db.refresh(needle)

    return {
        "value": needle.current_value,
        "pushes_up_total": needle.pushes_up_total,
        "pushes_down_total": needle.pushes_down_total,
        "year_split": _year_directional_split(db, source, song_id, year),
        "eligible_to_push": False,  # they just pushed
        "current_year": year,
        "review_case_opened": bool(case and case.id is not None),
    }
