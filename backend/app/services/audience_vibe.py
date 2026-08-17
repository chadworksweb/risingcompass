"""Audience Vibe service — push application, eligibility, threshold trigger.

Two needles, one song. The compass is diagnostic; the vibe is democratic.
The gap between them IS the data. This service handles the mechanics of how
pushes move the needle and when the gap opens an admin review case.

v1 identity model is device-id-only (anonymous, gameable at small scale —
roadmap accepts that "scale is the defense" until real-account auth ships).
The schema reserves a nullable user_id column so account-based gating can
layer in without a migration.

Unified song-entity renovation (Phase 5b): needles / pushes / review-cases are
keyed on the atomic song via `unified_song_id`. Callers still pass the
(source, song_id) the song page carries (now source='songs' + the unified id);
legacy pairs resolve through song_id_map. Writes stamp song_source='songs' +
the unified id so every vote for a song aggregates onto one needle.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    AudienceVibeNeedle, AudienceVibePush, AudienceVibeReviewCase, SongSlug, Song,
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


class VibeError(Exception):
    """Domain error from the vibe service. Message is safe to surface to the caller."""


def _resolve_song(db: Session, source: str, song_id: int) -> Song:
    """Resolve an incoming (source, song_id) to the unified Song. source='songs'
    is the unified id directly; a legacy pair maps via song_id_map."""
    if source == "songs":
        row = db.query(Song).get(song_id)
    else:
        nid = db.execute(
            text("SELECT new_song_id FROM song_id_map WHERE old_source = :s AND old_id = :i"),
            {"s": source, "i": song_id},
        ).scalar()
        row = db.query(Song).get(nid) if nid else None
    if not row:
        raise VibeError(f"{source} song id={song_id} not found")
    return row


def _initial_needle_value(song: Optional[Song]) -> int:
    """Initial audience needle value = compass charge so the offset starts at
    0. Falls back to 0 for uncalibrated songs."""
    return int((song.charge_value if song else None) or 0)


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


def _get_or_create_needle(db: Session, unified_id: int, song: Song) -> AudienceVibeNeedle:
    needle = (
        db.query(AudienceVibeNeedle)
        .filter(AudienceVibeNeedle.song_id == unified_id)
        .first()
    )
    if needle:
        return needle
    needle = AudienceVibeNeedle(
        song_id=unified_id,
        current_value=_initial_needle_value(song),
        pushes_up_total=0, pushes_down_total=0, pushes_agree_total=0,
    )
    db.add(needle)
    db.flush()
    return needle


def _check_eligibility(db: Session, unified_id: int, device_id: str, year: int) -> bool:
    """True if this device has not already pushed this song this year."""
    if not device_id:
        return False
    existing = (
        db.query(AudienceVibePush.id)
        .filter(AudienceVibePush.song_id == unified_id)
        .filter(AudienceVibePush.device_id == device_id)
        .filter(AudienceVibePush.push_year == year)
        .first()
    )
    return existing is None


def _year_directional_split(db: Session, unified_id: int, year: int) -> dict:
    """Counts of pushes in the current year, by direction."""
    rows = (
        db.query(AudienceVibePush.direction, func.count(AudienceVibePush.id))
        .filter(AudienceVibePush.song_id == unified_id)
        .filter(AudienceVibePush.push_year == year)
        .group_by(AudienceVibePush.direction)
        .all()
    )
    counts = {1: 0, 0: 0, -1: 0}
    for direction, n in rows:
        counts[int(direction)] = int(n)
    return {"up": counts[1], "agree": counts[0], "down": counts[-1]}


def _maybe_open_review_case(
    db: Session, unified_id: int, song: Song, vibe_value: int,
) -> Optional[AudienceVibeReviewCase]:
    """If the gap exceeds threshold, open a review case (or update the existing
    open one with the latest snapshot). Only one open case per song.
    """
    threshold = settings.vibe_review_threshold
    compass_charge = song.charge_value
    compass_color = song.rubric_color
    if compass_charge is None:
        return None

    gap = abs(compass_charge - vibe_value)
    if gap <= threshold:
        return None

    existing = (
        db.query(AudienceVibeReviewCase)
        .filter(AudienceVibeReviewCase.song_id == unified_id)
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
        song_id=unified_id,
        compass_charge=compass_charge, compass_color=compass_color,
        vibe_value=vibe_value, gap=gap, status="open",
    )
    db.add(case)
    return case


def get_state(db: Session, source: str, song_id: int, device_id: Optional[str]) -> dict:
    """Public read of the vibe state for one song."""
    song = _resolve_song(db, source, song_id)  # 404-fail if song missing
    unified_id = song.id
    needle = (
        db.query(AudienceVibeNeedle)
        .filter(AudienceVibeNeedle.song_id == unified_id)
        .first()
    )
    year = datetime.now(timezone.utc).year

    if not needle:
        eligible = bool(device_id)
        return {
            # Synthesise the initial value (= compass charge) so the frontend
            # offset visualization shows 0 before any push has been recorded.
            "value": _initial_needle_value(song),
            "pushes_up_total": 0,
            "pushes_down_total": 0,
            "pushes_agree_total": 0,
            "year_split": {"up": 0, "agree": 0, "down": 0},
            "eligible_to_push": eligible,
            "current_year": year,
        }

    eligible = _check_eligibility(db, unified_id, device_id, year) if device_id else False
    return {
        "value": needle.current_value,
        "pushes_up_total": needle.pushes_up_total,
        "pushes_down_total": needle.pushes_down_total,
        "pushes_agree_total": getattr(needle, "pushes_agree_total", 0) or 0,
        "year_split": _year_directional_split(db, unified_id, year),
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
    unified_id = song.id
    compass_charge = song.charge_value
    year = datetime.now(timezone.utc).year

    if not _check_eligibility(db, unified_id, device_id, year):
        raise VibeError("Already pushed this song this year. Each person gets one push per song per year.")

    needle = _get_or_create_needle(db, unified_id, song)

    push = AudienceVibePush(
        song_id=unified_id,
        direction=direction,
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
    needle.last_push_at = datetime.now(timezone.utc)
    needle.updated_at = needle.last_push_at

    case = _maybe_open_review_case(db, unified_id, song, new_value)

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
        "year_split": _year_directional_split(db, unified_id, year),
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

    # Cache needles + slugs + songs per unified id so repeat votes hit the DB once.
    needle_cache: dict = {}
    slug_cache: dict = {}
    song_cache: dict = {}

    def _needle_value(unified_id):
        if unified_id not in needle_cache:
            needle = (
                db.query(AudienceVibeNeedle)
                .filter(AudienceVibeNeedle.song_id == unified_id)
                .first()
            )
            needle_cache[unified_id] = needle.current_value if needle else None
        return needle_cache[unified_id]

    def _slug(unified_id):
        # Slugs live in the song_slugs table, not on the song row.
        if unified_id not in slug_cache:
            row = (
                db.query(SongSlug)
                .filter(SongSlug.song_id == unified_id)
                .first()
            )
            slug_cache[unified_id] = row.slug if row else None
        return slug_cache[unified_id]

    def _song(unified_id):
        if unified_id not in song_cache:
            song_cache[unified_id] = db.query(Song).get(unified_id) if unified_id else None
        return song_cache[unified_id]

    items = []
    for p in pushes:
        unified_id = p.song_id
        entry = {
            "song_source": "songs",
            "song_id": unified_id,
            "direction": p.direction,
            "direction_label": _DIRECTION_LABEL.get(p.direction, "agree"),
            "push_year": p.push_year,
            "pushed_at": p.pushed_at.isoformat() if p.pushed_at else None,
            "current_value": _needle_value(unified_id),
            "song_slug": _slug(unified_id),
        }
        song = _song(unified_id)
        if song:
            entry["song_title"] = song.title
            entry["song_artist"] = song.artist
            entry["compass_charge"] = song.charge_value
        else:
            entry["song_title"] = None
        items.append(entry)

    return {"count": len(items), "items": items}
