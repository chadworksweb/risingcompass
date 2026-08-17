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

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func

from app.database import SessionLocal
from app.models import (
    AlbumCalibration,
    Artist,
    Chart,
    ChartAppearance,
    MisreadSubmission,
    Song,
)
from app.constants import COLOR_LABELS, COLOR_HEX

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/badge", tags=["badge"])


def _song_has_pending_flag(db, *, title_lower: str, artist_lower: str) -> bool:
    """True if any misread submission against this song (matched by
    case-insensitive title + artist) is still pending."""
    q = db.query(MisreadSubmission.id).filter(
        MisreadSubmission.status == "pending",
        func.lower(MisreadSubmission.song_title) == title_lower,
        func.lower(MisreadSubmission.song_artist) == artist_lower,
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


def _effects_pl_slugs(raw) -> list:
    """Decode songs.effects_pl (JSON slug list) to a clean, canonically-ordered
    slug list, dropping anything not in the current vocabulary."""
    from app.services.effects_pl_vocab import clean_effects_pl
    return clean_effects_pl(_parse_json(raw) or [])


def _effects_pl_labels(raw) -> list:
    """Resolve songs.effects_pl to [{slug, label, shadow}] (canonical order) so
    consumers render + style the effect tags without their own vocabulary."""
    from app.services.effects_pl_vocab import effects_pl_detail
    return effects_pl_detail(_parse_json(raw) or [])


def _find_calibration(title: str, artist: str, db) -> dict | None:
    """Look up a song's full per-song record in the unified `songs` table.
    Exact case-insensitive (title, artist) first, then punctuation-fuzzy on
    title. Chart/catalog context comes from the song's most-recent chart
    appearance (a song can chart in many years); None for non-charting songs."""
    title_lower = title.lower()
    artist_lower = artist.lower()
    title_stripped = re.sub(r"[^\w\s]", "", title_lower)

    row = (
        db.query(Song)
        .filter(func.lower(Song.title) == title_lower)
        .filter(func.lower(Song.artist) == artist_lower)
        .order_by(Song.id.desc())
        .first()
    )
    if not row:
        candidates = db.query(Song).filter(func.lower(Song.artist) == artist_lower).all()
        for c in candidates:
            if re.sub(r"[^\w\s]", "", c.title.lower()) == title_stripped:
                row = c
                break

    if not (row and row.rubric_color and row.charge_value is not None):
        return None

    pending = _song_has_pending_flag(
        db, title_lower=row.title.lower(), artist_lower=(row.artist or "").lower(),
    )

    # Chart/catalog context from the most-recent appearance (best position tie-break).
    from app.services.song_store import decade_of
    appr = (
        db.query(ChartAppearance, Chart.slug)
        .join(Chart, Chart.id == ChartAppearance.chart_id)
        .filter(ChartAppearance.song_id == row.id)
        .order_by(ChartAppearance.year.desc().nullslast(),
                  ChartAppearance.position.asc().nullslast())
        .first()
    )
    if appr:
        ca, chart_slug = appr
        ctx_year, ctx_pos = ca.year, ca.position
        ctx_decade = decade_of(ca.year) if ca.year else None
        ctx_letter = ca.position_letter or None
        ctx_source = chart_slug
    else:
        ctx_year = ctx_decade = ctx_pos = ctx_letter = ctx_source = None

    from app.services.artist_utils import generate_song_slug
    return {
        # --- Identity ---
        "title": row.title,
        "artist": row.artist,
        "song_source": "songs",  # unified: one Library table
        "song_slug": generate_song_slug(row.title, row.artist or ""),
        # --- Classification ---
        "tier": row.rubric_color,
        "tier_label": COLOR_LABELS.get(row.rubric_color, ""),
        "tier_hex": COLOR_HEX.get(row.rubric_color, "#999"),
        "charge": row.charge_value,
        "charge_summary": row.charge_summary,
        "confidence": row.confidence,
        "contaminated": row.contaminated or False,
        "contamination_note": row.contamination_note,
        "dogma_referenced": row.dogma_referenced or False,
        "dogma_note": row.dogma_note,
        "calibration_failed": row.calibration_failed or False,
        "instrumental": row.instrumental,
        "pending": pending,
        # --- Ether Art Chart ---
        "deadpan_line": row.deadpan_line,
        "topics": _parse_json(row.topics),
        "topic_audit": _parse_json(row.topic_audit),
        # Psyche Facts family (prescription bundle); JSON-decoded from the Text
        # column so consumers (chadlewine SongLabel) get the object directly.
        "psyche_facts": _parse_json(row.psyche_facts),
        # Per-listen effects tag axis: the stored slugs plus resolved display
        # labels (canonical order) from the RC-owned vocabulary, so consumers
        # (chadlewine / DBM proliferator) pull the labels from RC.
        "effects_pl": _effects_pl_slugs(row.effects_pl),
        "effects_pl_labels": _effects_pl_labels(row.effects_pl),
        # --- Prose / analysis ---
        "listener_effects_prose": row.listener_effects_prose,
        "societal_effects_prose": row.societal_effects_prose,
        "message_analysis": row.message_analysis,
        "expression_analysis": row.expression_analysis,
        "intention_analysis": row.intention_analysis,
        "activations": _parse_json(row.activations),
        # --- Chart / catalog context (from most-recent appearance; None if non-charting) ---
        "year": ctx_year,
        "decade": ctx_decade,
        "chart_position": ctx_pos,
        "chart_position_letter": ctx_letter,
        "chart_source": ctx_source,
        "track_number": row.track_number,
        "source": row.canonical_calibration_method,
        "calibrated_at": row.created_at.isoformat() if row.created_at else None,
    }


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
    (deadpan_line / topics / topic_audit), prose + analysis (listener_effects_prose /
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
