"""Badge API — public lookup endpoints for embedded Rising Compass badges.

Song lookup: searches compass_songs, library_songs, submitted_songs and
returns the FULL per-song record (classification, Ether Art Chart, prose,
analysis, chart/catalog context) for API consumers, not just the badge
slice. The legacy badge keys (tier/charge/charge_summary/...) are preserved
so existing badge embeds keep working.
Album lookup: returns stored album calibration (computed mean of tracks).
Album calibrate: computes + stores album calibration from track lookups.
"""

import re
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, or_

from app.database import SessionLocal
from app.models import (
    AlbumCalibration,
    Artist,
    CompassSong,
    LibrarySong,
    MisreadSubmission,
    SongSlug,
    SubmittedSong,
)
from app.constants import COLOR_LABELS, COLOR_HEX

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/badge", tags=["badge"])


_SOURCE_FOR_MODEL = {
    CompassSong: "compass",
    LibrarySong: "library",
    SubmittedSong: "submitted",
}


def _song_has_pending_flag(
    db, *, song_source: str, song_id: int, title_lower: str, artist_lower: str
) -> bool:
    """True if any misread submission against this song is still pending
    (not yet reviewed, accepted, or rejected). Matches by polymorphic
    (song_source, song_id) first — falls back to case-insensitive
    (song_title, song_artist) for legacy rows with null polymorphic keys.
    """
    q = db.query(MisreadSubmission.id).filter(
        MisreadSubmission.status == "pending"
    ).filter(
        or_(
            and_(
                MisreadSubmission.song_source == song_source,
                MisreadSubmission.song_id == song_id,
            ),
            and_(
                MisreadSubmission.song_source.is_(None),
                func.lower(MisreadSubmission.song_title) == title_lower,
                func.lower(MisreadSubmission.song_artist) == artist_lower,
            ),
        )
    )
    return db.query(q.exists()).scalar() or False


def _parse_json(raw):
    """Best-effort decode of a JSON-encoded text column (topics, activations,
    topic_audit). Returns the parsed value, or None when the column is empty
    or not valid JSON, so a malformed row never 500s a lookup."""
    if not raw:
        return None
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _find_calibration(title: str, artist: str, db) -> dict | None:
    """Search all calibration tables for a match. Returns the full per-song
    record (or None). Fields absent on a given table resolve to None via
    getattr, so the same shape comes back regardless of which table matched."""
    title_lower = title.lower()
    artist_lower = artist.lower()
    title_stripped = re.sub(r"[^\w\s]", "", title_lower)

    # Search order: compass_songs → library_songs → submitted_songs
    for Model in (CompassSong, LibrarySong, SubmittedSong):
        # Exact match
        row = (
            db.query(Model)
            .filter(func.lower(Model.title) == title_lower)
            .filter(func.lower(Model.artist) == artist_lower)
            .order_by(Model.id.desc())
            .first()
        )

        # Fuzzy match (strip punctuation)
        if not row:
            candidates = (
                db.query(Model)
                .filter(func.lower(Model.artist) == artist_lower)
                .all()
            )
            for c in candidates:
                if re.sub(r"[^\w\s]", "", c.title.lower()) == title_stripped:
                    row = c
                    break

        if row and row.rubric_color and row.charge_value is not None:
            source = _SOURCE_FOR_MODEL[Model]
            pending = _song_has_pending_flag(
                db,
                song_source=source,
                song_id=row.id,
                title_lower=row.title.lower(),
                artist_lower=(row.artist or "").lower(),
            )
            slug_row = (
                db.query(SongSlug.slug)
                .filter(SongSlug.song_source == source)
                .filter(SongSlug.song_id == row.id)
                .order_by(SongSlug.id.desc())
                .first()
            )
            song_slug = slug_row[0] if slug_row else None
            # created_at on compass/library, submitted_at on submitted.
            created = getattr(row, "created_at", None) or getattr(row, "submitted_at", None)
            return {
                # --- Identity ---
                "title": row.title,
                "artist": row.artist,
                # Which calibration table this resolved against
                # (compass | library | submitted).
                "song_source": source,
                # Canonical RC URL slug so consumers can deep-link to the
                # specific song page (risingcompass.net/songs/<slug>) instead
                # of the RC homepage. Null when no slug row exists yet for this
                # (source, id) — caller should fall back.
                "song_slug": song_slug,
                # --- Classification ---
                "tier": row.rubric_color,
                "tier_label": COLOR_LABELS.get(row.rubric_color, ""),
                "tier_hex": COLOR_HEX.get(row.rubric_color, "#999"),
                "charge": row.charge_value,
                "charge_summary": getattr(row, "charge_summary", None),
                "confidence": getattr(row, "confidence", None),
                "contaminated": getattr(row, "contaminated", False) or False,
                "contamination_note": getattr(row, "contamination_note", None),
                "dogma_referenced": getattr(row, "dogma_referenced", False) or False,
                "dogma_note": getattr(row, "dogma_note", None),
                "calibration_failed": getattr(row, "calibration_failed", False) or False,
                "instrumental": getattr(row, "instrumental", None),
                # True when this song has an open misread/satirical flag that
                # hasn't been resolved. Consumers can render a "PENDING" stamp
                # to indicate the score is being contested.
                "pending": pending,
                # --- Ether Art Chart ---
                # deadpan_line: flat literal naming of the song.
                # topics: taxonomy slugs, dominant-first (decoded to a list).
                "deadpan_line": getattr(row, "deadpan_line", None),
                "topics": _parse_json(getattr(row, "topics", None)),
                "topic_audit": _parse_json(getattr(row, "topic_audit", None)),
                # --- Prose / analysis ---
                "effects_prose": getattr(row, "effects_prose", None),
                "societal_effects_prose": getattr(row, "societal_effects_prose", None),
                "message_analysis": getattr(row, "message_analysis", None),
                "expression_analysis": getattr(row, "expression_analysis", None),
                "intention_analysis": getattr(row, "intention_analysis", None),
                "activations": _parse_json(getattr(row, "activations", None)),
                # --- Chart / catalog context (table-specific; None elsewhere) ---
                "year": getattr(row, "year", None),
                "decade": getattr(row, "decade", None),
                "chart_position": getattr(row, "chart_position", None),
                "chart_position_letter": getattr(row, "chart_position_letter", None),
                "chart_source": getattr(row, "chart_source", None),
                "track_number": getattr(row, "track_number", None),
                "source": getattr(row, "source", None),
                "calibrated_at": created.isoformat() if created else None,
            }

    return None


def _derive_tier(charge: int) -> tuple[str, str, str]:
    """Derive rubric_color, tier_label, tier_hex from a charge value."""
    tiers = [
        (75, "violet"), (25, "blue"), (-24, "green"),
        (-74, "orange"), (-100, "red"),
    ]
    color = "red"
    for threshold, c in tiers:
        if charge >= threshold:
            color = c
            break
    return color, COLOR_LABELS[color], COLOR_HEX[color]


@router.get("/lookup")
def badge_lookup(
    title: str = Query(..., min_length=1),
    artist: str = Query(..., min_length=1),
):
    """Look up a song's full Rising Compass record for badge + data display.

    Returns the complete per-song payload: classification (tier / charge /
    charge_summary / contamination / dogma / confidence), Ether Art Chart
    (deadpan_line / topics / topic_audit), prose + analysis (effects_prose /
    societal_effects_prose / message|expression|intention_analysis /
    activations), and chart/catalog context. Does not calibrate — lookup only.
    """
    db = SessionLocal()
    try:
        result = _find_calibration(title.strip(), artist.strip(), db)
        if not result:
            raise HTTPException(404, "No calibration found for this song")
        return result
    finally:
        db.close()


# --- Album endpoints ---


class AlbumCalibrateRequest(BaseModel):
    title: str
    artist: str
    track_titles: list[str]


@router.post("/album-calibrate")
def album_calibrate(req: AlbumCalibrateRequest):
    """Compute and store an album calibration from its track calibrations.

    Looks up each track, computes mean charge, derives tier, upserts into
    album_calibrations. Tracks not found are skipped (but counted).
    """
    db = SessionLocal()
    try:
        title = req.title.strip()
        artist = req.artist.strip()

        charges = []
        contaminated_count = 0
        missing = []

        for track_title in req.track_titles:
            result = _find_calibration(track_title.strip(), artist, db)
            if result:
                charges.append(result["charge"])
                if result.get("contaminated"):
                    contaminated_count += 1
            else:
                missing.append(track_title)

        if not charges:
            raise HTTPException(
                400,
                f"No calibrated tracks found for album '{title}' by '{artist}'. "
                f"Missing: {missing}",
            )

        avg_charge = round(sum(charges) / len(charges))
        rubric_color, tier_label, tier_hex = _derive_tier(avg_charge)

        summary = f"Album aggregate across {len(charges)} calibrated tracks."
        if missing:
            summary += f" {len(missing)} tracks uncalibrated."
        if contaminated_count:
            summary += f" {contaminated_count} contaminated."

        # Upsert
        existing = (
            db.query(AlbumCalibration)
            .filter(func.lower(AlbumCalibration.title) == title.lower())
            .filter(func.lower(AlbumCalibration.artist) == artist.lower())
            .first()
        )

        if existing:
            existing.rubric_color = rubric_color
            existing.charge_value = avg_charge
            existing.charge_summary = summary
            existing.track_count = len(charges)
            existing.contamination_count = contaminated_count
            existing.updated_at = datetime.utcnow()
        else:
            existing = AlbumCalibration(
                title=title,
                artist=artist,
                rubric_color=rubric_color,
                charge_value=avg_charge,
                charge_summary=summary,
                track_count=len(charges),
                contamination_count=contaminated_count,
            )
            db.add(existing)

        db.commit()

        return {
            "title": title,
            "artist": artist,
            "tier": rubric_color,
            "tier_label": tier_label,
            "tier_hex": tier_hex,
            "charge": avg_charge,
            "charge_summary": summary,
            "track_count": len(charges),
            "contamination_count": contaminated_count,
            "missing_tracks": missing,
        }
    finally:
        db.close()


@router.get("/album-lookup")
def album_lookup(
    title: str = Query(..., min_length=1),
    artist: str = Query(..., min_length=1),
):
    """Look up a stored album calibration for badge display."""
    db = SessionLocal()
    try:
        row = (
            db.query(AlbumCalibration)
            .filter(func.lower(AlbumCalibration.title) == title.lower())
            .filter(func.lower(AlbumCalibration.artist) == artist.lower())
            .first()
        )
        if not row:
            raise HTTPException(404, "No album calibration found")

        tier_label = COLOR_LABELS.get(row.rubric_color, "")
        tier_hex = COLOR_HEX.get(row.rubric_color, "#999")

        # Canonical artist slug so consumers can deep-link the album badge
        # to the artist's RC trajectory page (no first-class album pages on
        # RC; the artist page is the next-best target above homepage).
        artist_row = (
            db.query(Artist.slug)
            .filter(func.lower(Artist.name) == (row.artist or "").lower())
            .first()
        )
        artist_slug = artist_row[0] if artist_row else None

        return {
            "title": row.title,
            "artist": row.artist,
            "tier": row.rubric_color,
            "tier_label": tier_label,
            "tier_hex": tier_hex,
            "charge": row.charge_value,
            "charge_summary": row.charge_summary,
            "track_count": row.track_count,
            "contamination_count": row.contamination_count,
            "contaminated": row.contamination_count > 0,
            "contamination_note": (
                f"{row.contamination_count} of {row.track_count} tracks carry contamination."
                if row.contamination_count > 0 else None
            ),
            "artist_slug": artist_slug,
        }
    finally:
        db.close()
