"""Badge API — public lookup endpoint for embedded Rising Compass badges.

Returns cached classification data for a song by title + artist.
Searches compass_songs, library_songs, and submitted_songs (in that order).
Does NOT trigger new classifications — only returns existing data.
"""

import re
import logging

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func

from app.database import SessionLocal
from app.models import CompassSong, LibrarySong, SubmittedSong
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
