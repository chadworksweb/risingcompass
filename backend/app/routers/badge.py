"""Badge API — public lookup endpoints for embedded Rising Compass badges.

Song lookup: searches compass_songs, library_songs, submitted_songs.
Album lookup: returns stored album classification (computed mean of tracks).
Album classify: computes + stores album classification from track lookups.
"""

import re
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func

from app.database import SessionLocal
from app.models import CompassSong, LibrarySong, SubmittedSong, AlbumClassification
from app.constants import COLOR_LABELS, COLOR_HEX

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/badge", tags=["badge"])


def _find_classification(title: str, artist: str, db) -> dict | None:
    """Search all classification tables for a match. Returns dict or None."""
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
            return {
                "title": row.title,
                "artist": row.artist,
                "tier": row.rubric_color,
                "tier_label": COLOR_LABELS.get(row.rubric_color, ""),
                "tier_hex": COLOR_HEX.get(row.rubric_color, "#999"),
                "charge": row.charge_value,
                "contaminated": getattr(row, "contaminated", False) or False,
                "contamination_note": getattr(row, "contamination_note", None),
                "charge_summary": getattr(row, "charge_summary", None),
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
    """Look up a song's Rising Compass classification for badge display.

    Returns tier, charge, summary, and hex color. Does not classify — lookup only.
    """
    db = SessionLocal()
    try:
        result = _find_classification(title.strip(), artist.strip(), db)
        if not result:
            raise HTTPException(404, "No classification found for this song")
        return result
    finally:
        db.close()


# --- Album endpoints ---


class AlbumClassifyRequest(BaseModel):
    title: str
    artist: str
    track_titles: list[str]


@router.post("/album-classify")
def album_classify(req: AlbumClassifyRequest):
    """Compute and store an album classification from its track classifications.

    Looks up each track, computes mean charge, derives tier, upserts into
    album_classifications. Tracks not found are skipped (but counted).
    """
    db = SessionLocal()
    try:
        title = req.title.strip()
        artist = req.artist.strip()

        charges = []
        contaminated_count = 0
        missing = []

        for track_title in req.track_titles:
            result = _find_classification(track_title.strip(), artist, db)
            if result:
                charges.append(result["charge"])
                if result.get("contaminated"):
                    contaminated_count += 1
            else:
                missing.append(track_title)

        if not charges:
            raise HTTPException(
                400,
                f"No classified tracks found for album '{title}' by '{artist}'. "
                f"Missing: {missing}",
            )

        avg_charge = round(sum(charges) / len(charges))
        rubric_color, tier_label, tier_hex = _derive_tier(avg_charge)

        summary = f"Album aggregate across {len(charges)} classified tracks."
        if missing:
            summary += f" {len(missing)} tracks unclassified."
        if contaminated_count:
            summary += f" {contaminated_count} contaminated."

        # Upsert
        existing = (
            db.query(AlbumClassification)
            .filter(func.lower(AlbumClassification.title) == title.lower())
            .filter(func.lower(AlbumClassification.artist) == artist.lower())
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
            existing = AlbumClassification(
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
    """Look up a stored album classification for badge display."""
    db = SessionLocal()
    try:
        row = (
            db.query(AlbumClassification)
            .filter(func.lower(AlbumClassification.title) == title.lower())
            .filter(func.lower(AlbumClassification.artist) == artist.lower())
            .first()
        )
        if not row:
            raise HTTPException(404, "No album classification found")

        tier_label = COLOR_LABELS.get(row.rubric_color, "")
        tier_hex = COLOR_HEX.get(row.rubric_color, "#999")

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
        }
    finally:
        db.close()
