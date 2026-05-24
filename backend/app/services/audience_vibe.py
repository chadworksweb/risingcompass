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
    CompassSong, LibrarySong, SubmittedSong, SongSlug,
)

logger = logging.getLogger(__name__)

VIBE_MIN = -100
VIBE_MAX = 100

# Bounded consensus model. The needle is recomputed from the vote tallies on
# every push (it is NOT incremented), so it can never run away:
#
#     offset = round( W * (up - down) / (up + agree + down + k) )
#     value  = clamp(compass + offset, VIBE_MIN, VIBE_MAX)
#
# W caps the furthest the audience can ever pull the song from the compass
# score; agree votes only grow the denominator, anchoring toward compass; k
# is inertia, keeping small samples near compass until a real crowd forms.
VIBE_WINDOW = 15   # W — max swing from compass in either direction
VIBE_INERTIA = 8   # k — votes' worth of pull-toward-compass before it commits

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


def _consensus_value(compass: Optional[int], up: int, agree: int, down: int) -> int:
    """Bounded, inertia-damped consensus position for the audience needle.

    offset = round( W * (up - down) / (up + agree + down + k) ), clamped so the
    audience can never pull the song more than W points off the compass score.
    Agree votes anchor toward compass (denominator only). With a compass score
    of None (uncalibrated song) the needle floats around 0.
    """
    anchor = int(compass or 0)
    total = up + agree + down
    if total <= 0:
        return max(VIBE_MIN, min(VIBE_MAX, anchor))
    offset = round(VIBE_WINDOW * (up - down) / (total + VIBE_INERTIA))
    return max(VIBE_MIN, min(VIBE_MAX, anchor + offset))


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
        pushes_up_total=0, pushes_down_total=0, pushes_agree_total=0,
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
    counts = {1: 0, 0: 0, -1: 0}
    for direction, n in rows:
        counts[int(direction)] = int(n)
    return {"up": counts[1], "agree": counts[0], "down": counts[-1]}


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
            "pushes_agree_total": 0,
            "year_split": {"up": 0, "agree": 0, "down": 0},
            "eligible_to_push": eligible,
            "current_year": year,
        }

    eligible = _check_eligibility(db, source, song_id, device_id, year) if device_id else False
    return {
        "value": needle.current_value,
        "pushes_up_total": needle.pushes_up_total,
        "pushes_down_total": needle.pushes_down_total,
        "pushes_agree_total": getattr(needle, "pushes_agree_total", 0) or 0,
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
    user_id: Optional[int] = None,
) -> dict:
    """Apply one push. Enforces eligibility, updates the needle, opens a review
    case if the gap crosses threshold. Returns the updated state.

    Eligibility stays device-scoped so anonymous voting works exactly as
    before; user_id is stamped on the push when the voter happens to be signed
    in, purely so they can review their own activity later. It is never
    required and never gates the vote.
    """
    if direction not in (1, 0, -1):
        raise VibeError("direction must be +1 (higher), 0 (agree), or -1 (lower)")
    if not device_id:
        raise VibeError("device_id is required to push the vibe")

    song = _resolve_song(db, source, song_id)  # validate target
    compass_charge = getattr(song, "charge_value", None)
    year = datetime.utcnow().year

    if not _check_eligibility(db, source, song_id, device_id, year):
        raise VibeError("Already pushed this song this year. Each person gets one push per song per year.")

    needle = _get_or_create_needle(db, source, song_id)

    push = AudienceVibePush(
        song_source=source, song_id=song_id, direction=direction,
        device_id=device_id, ip_address=ip_address, push_year=year,
        user_id=user_id,
    )
    db.add(push)

    if direction == 1:
        needle.pushes_up_total += 1
    elif direction == -1:
        needle.pushes_down_total += 1
    else:
        needle.pushes_agree_total = (needle.pushes_agree_total or 0) + 1

    # Recompute the needle from the full tallies — never increment. This is
    # what makes overshoot structurally impossible (see _consensus_value).
    new_value = _consensus_value(
        compass_charge,
        needle.pushes_up_total,
        needle.pushes_agree_total or 0,
        needle.pushes_down_total,
    )
    needle.current_value = new_value
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
        "pushes_agree_total": needle.pushes_agree_total or 0,
        "year_split": _year_directional_split(db, source, song_id, year),
        "eligible_to_push": False,  # they just pushed
        "current_year": year,
        "review_case_opened": bool(case and case.id is not None),
    }


_DIRECTION_LABEL = {1: "higher", 0: "agree", -1: "lower"}


def get_user_activity(db: Session, user_id: int, limit: int = 100) -> dict:
    """Every vibe vote this signed-in user has cast, newest first, joined to
    the song it landed on and where that song's needle sits now.

    Only votes made while signed in carry a user_id, so anonymous votes the
    same person cast on another device won't appear here — by design.
    """
    pushes = (
        db.query(AudienceVibePush)
        .filter(AudienceVibePush.user_id == user_id)
        .order_by(AudienceVibePush.pushed_at.desc())
        .limit(limit)
        .all()
    )

    # Cache needles + slugs per (source, id) so repeat votes hit the DB once.
    needle_cache: dict = {}
    slug_cache: dict = {}

    def _needle_value(source: str, song_id: int):
        key = (source, song_id)
        if key not in needle_cache:
            needle = (
                db.query(AudienceVibeNeedle)
                .filter(AudienceVibeNeedle.song_source == source)
                .filter(AudienceVibeNeedle.song_id == song_id)
                .first()
            )
            needle_cache[key] = needle.current_value if needle else None
        return needle_cache[key]

    def _slug(source: str, song_id: int):
        # Slugs live in the song_slugs table, not on the song row.
        key = (source, song_id)
        if key not in slug_cache:
            row = (
                db.query(SongSlug)
                .filter(SongSlug.song_source == source)
                .filter(SongSlug.song_id == song_id)
                .first()
            )
            slug_cache[key] = row.slug if row else None
        return slug_cache[key]

    items = []
    for p in pushes:
        entry = {
            "song_source": p.song_source,
            "song_id": p.song_id,
            "direction": p.direction,
            "direction_label": _DIRECTION_LABEL.get(p.direction, "agree"),
            "push_year": p.push_year,
            "pushed_at": p.pushed_at.isoformat() if p.pushed_at else None,
            "current_value": _needle_value(p.song_source, p.song_id),
            "song_slug": _slug(p.song_source, p.song_id),
        }
        try:
            song = _resolve_song(db, p.song_source, p.song_id)
            entry["song_title"] = song.title
            entry["song_artist"] = getattr(song, "artist", None)
            entry["compass_charge"] = getattr(song, "charge_value", None)
        except VibeError:
            entry["song_title"] = None
        items.append(entry)

    return {"count": len(items), "items": items}
