"""Calibration Log — unified feed read layer.

Phase 3 of the Calibration Log (see RISING-COMPASS-CALIBRATION-LOG.md).
Normalizes rows from every capture table into a single feed-entry shape
so the public API, per-song sections, and homepage mini-feed can all
render from one contract.

Capture tables today:
  - pre_publish_corrections (Phase 1)
  - song_recalibrations      (Phase 2)

Future tables (rubric_updates, vibe_resolutions) get adapter functions
here; callers never change.

Read side only. Write side is owned by the capture routers
(agent.py /correct, recalibrations.py /start, calibration_log.py /promote).
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.models import (
    CompassSong, LibrarySong, SubmittedSong, StreamSong, SongSlug,
    PrePublishCorrection, SongRecalibration,
)
from app.services.artist_utils import generate_song_slug


_SONG_MODELS = {
    "compass": CompassSong,
    "library": LibrarySong,
    "submitted": SubmittedSong,
    "stream": StreamSong,
}


def _lookup_song_anchor(
    db: Session,
    song_source: Optional[str],
    song_id: Optional[int],
    slug_cache: dict,
) -> Optional[dict]:
    """Resolve (source, id) → title/artist/slug. Returns None if the song is
    gone (trashed, cascaded) — feed entry stays standalone but still renders."""
    if not (song_source and song_id):
        return None
    model = _SONG_MODELS.get(song_source)
    if not model:
        return None
    row = db.query(model).filter(model.id == song_id).first()
    if not row:
        return None

    cache_key = (song_source, song_id)
    slug = slug_cache.get(cache_key)
    if slug is None:
        slug_row = (
            db.query(SongSlug)
            .filter(SongSlug.song_source == song_source)
            .filter(SongSlug.song_id == song_id)
            .first()
        )
        slug = slug_row.slug if slug_row else generate_song_slug(row.title, row.artist)
        slug_cache[cache_key] = slug

    return {
        "song_source": song_source,
        "song_id": song_id,
        "title": row.title,
        "artist": row.artist,
        "slug": slug,
    }


def _correction_to_entry(
    row: PrePublishCorrection,
    db: Session,
    slug_cache: dict,
) -> dict:
    """Adapter: pre_publish_corrections row → normalized feed entry."""
    anchor = _lookup_song_anchor(db, "compass", row.compass_song_id, slug_cache)
    if anchor:
        title = f"{anchor['title']} — {anchor['artist']}"
    else:
        title = "Pre-publish correction"
    return {
        "event_id": row.id,
        "event_type": "pre_publish_correction",
        "source_table": "pre_publish_corrections",
        "pipeline": None,
        "lens": None,
        "occurred_at": row.occurred_at,
        "song_anchor": anchor,
        "title": title,
        "before": {
            "rubric_color": row.before_rubric_color,
            "charge_value": row.before_charge_value,
            "contaminated": bool(row.before_contaminated) if row.before_contaminated is not None else None,
            "contamination_note": row.before_contamination_note,
            "summary": row.before_summary,
        },
        "after": {
            "rubric_color": row.after_rubric_color,
            "charge_value": row.after_charge_value,
            "contaminated": bool(row.after_contaminated) if row.after_contaminated is not None else None,
            "contamination_note": row.after_contamination_note,
            "summary": row.after_summary,
        },
        "human_rationale": row.human_rationale,
        "ai_rationale": None,
        "public_summary": None,
        "rubric_change_note": None,
        "tags": row.tags,
        "promoted_to_feed": bool(row.promoted_to_feed),
        "promoted_at": row.promoted_at,
    }


def _recalibration_to_entry(
    row: SongRecalibration,
    db: Session,
    slug_cache: dict,
) -> dict:
    """Adapter: song_recalibrations row → normalized feed entry."""
    anchor = _lookup_song_anchor(db, row.song_source, row.song_id, slug_cache)
    if anchor:
        title = f"{anchor['title']} — {anchor['artist']}"
    else:
        title = f"Recalibration ({row.pipeline or 'unknown'})"
    return {
        "event_id": row.id,
        "event_type": "recalibration",
        "source_table": "song_recalibrations",
        "pipeline": row.pipeline,
        "lens": row.lens,
        "occurred_at": row.applied_at,
        "song_anchor": anchor,
        "title": title,
        "before": {
            "rubric_color": row.before_color,
            "charge_value": row.before_charge,
            "contaminated": None,
            "contamination_note": None,
            "summary": row.before_summary,
        },
        "after": {
            "rubric_color": row.after_color,
            "charge_value": row.after_charge,
            "contaminated": None,
            "contamination_note": None,
            "summary": None,
        },
        "human_rationale": row.human_rationale,
        "ai_rationale": row.ai_rationale,
        "public_summary": row.public_summary,
        "rubric_change_note": row.rubric_change_note,
        "tags": row.tags,
        "promoted_to_feed": bool(row.promoted_to_feed),
        "promoted_at": row.promoted_at,
    }


def list_feed_entries(
    db: Session,
    *,
    include_unpromoted: bool = False,
    event_types: Optional[Iterable[str]] = None,
    song_source: Optional[str] = None,
    song_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Return (entries, total) across all capture tables.

    Args:
        include_unpromoted: admin-only; public callers always pass False.
        event_types: restrict to these types (e.g. {"recalibration"}).
        song_source + song_id: restrict to one song's events.
        limit / offset: pagination.

    Entries are sorted by occurred_at DESC (chronological newest-first)
    across all tables, then sliced by limit/offset in Python after the
    per-table pulls. total is the pre-slice count.
    """
    types = set(event_types) if event_types else None
    slug_cache: dict = {}
    entries: list[dict] = []

    # Pre-publish corrections.
    if not types or "pre_publish_correction" in types:
        q = db.query(PrePublishCorrection)
        if not include_unpromoted:
            q = q.filter(PrePublishCorrection.promoted_to_feed == True)  # noqa: E712
        if song_source is not None and song_id is not None:
            # pre_publish corrections are always anchored to compass songs;
            # filter to matching compass_song_id only when source=compass.
            if song_source == "compass":
                q = q.filter(PrePublishCorrection.compass_song_id == song_id)
            else:
                q = q.filter(False)
        for row in q.all():
            entries.append(_correction_to_entry(row, db, slug_cache))

    # Song recalibrations.
    if not types or "recalibration" in types:
        q = db.query(SongRecalibration)
        if not include_unpromoted:
            q = q.filter(SongRecalibration.promoted_to_feed == True)  # noqa: E712
        if song_source is not None and song_id is not None:
            q = q.filter(SongRecalibration.song_source == song_source)
            q = q.filter(SongRecalibration.song_id == song_id)
        for row in q.all():
            entries.append(_recalibration_to_entry(row, db, slug_cache))

    entries.sort(key=lambda e: e["occurred_at"] or datetime.min, reverse=True)

    total = len(entries)
    window = entries[offset:offset + limit] if limit > 0 else entries[offset:]
    return window, total
