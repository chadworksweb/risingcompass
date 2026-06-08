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
    Artist, Song, SongSlug,
    PrePublishCorrection, SongRecalibration, RubricChange,
)
from sqlalchemy import func, text
from app.services.artist_utils import generate_song_slug, normalize_artist_name


def _to_unified_id(db: Session, song_source: Optional[str], song_id: Optional[int]) -> Optional[int]:
    """Resolve an incoming (source, id) to the unified songs.id. source='songs'
    is the id directly; a legacy pair maps via song_id_map."""
    if not song_id:
        return None
    if song_source == "songs" or song_source is None:
        return song_id if song_source == "songs" else None
    return db.execute(
        text("SELECT new_song_id FROM song_id_map WHERE old_source = :s AND old_id = :i"),
        {"s": song_source, "i": song_id},
    ).scalar()


def _lookup_song_anchor(
    db: Session,
    unified_id: Optional[int],
    slug_cache: dict,
) -> Optional[dict]:
    """Resolve a unified songs.id → title/artist/slug. Returns None if the song
    is gone (trashed, cascaded) — feed entry stays standalone but still renders."""
    if not unified_id:
        return None
    row = db.query(Song).filter(Song.id == unified_id).first()
    if not row:
        return None

    cache_key = ("songs", unified_id)
    slug = slug_cache.get(cache_key)
    if slug is None:
        slug_row = (
            db.query(SongSlug)
            .filter(SongSlug.song_id == unified_id)
            .first()
        )
        slug = slug_row.slug if slug_row else generate_song_slug(row.title, row.artist)
        slug_cache[cache_key] = slug

    artist_slug = None
    primary = normalize_artist_name(row.artist or "").lower()
    if primary:
        artist_cache_key = ("artist", primary)
        if artist_cache_key in slug_cache:
            artist_slug = slug_cache[artist_cache_key]
        else:
            artist_row = (
                db.query(Artist.slug)
                .filter(func.lower(Artist.name) == primary)
                .filter(Artist.slug.isnot(None))
                .first()
            )
            artist_slug = artist_row[0] if artist_row else None
            slug_cache[artist_cache_key] = artist_slug

    return {
        "song_source": "songs",
        "song_id": unified_id,
        "title": row.title,
        "artist": row.artist,
        "slug": slug,
        "artist_slug": artist_slug,
    }


def _correction_to_entry(
    row: PrePublishCorrection,
    db: Session,
    slug_cache: dict,
) -> dict:
    """Adapter: pre_publish_corrections row → normalized feed entry."""
    # compass_song_id now carries the unified songs.id (Phase 5b).
    anchor = _lookup_song_anchor(db, row.compass_song_id, slug_cache)
    if anchor:
        title = f"{anchor['title']} - {anchor['artist']}"
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
    anchor = _lookup_song_anchor(db, row.song_id, slug_cache)
    if anchor:
        title = f"{anchor['title']} - {anchor['artist']}"
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


_KIND_LABELS = {
    "tenet": "Tenet",
    "note": "Tenet note",
    "rule": "Rule",
    "modifier": "Modifier",
    "flag": "Flag",
    "schema": "Schema",
    "tier": "Tier",
}
_CHANGE_VERBS = {"added": "added", "revised": "revised", "retired": "retired"}


def linked_songs_by_slug(
    db: Session,
    change_slugs: Iterable[str],
    slug_cache: dict,
) -> dict:
    """For a set of rubric-change `change_slug`s, return
    {change_slug: [song_anchor, ...]} — the songs each change recalibrated, via
    song_recalibrations.rubric_change_slug. Deduped by song_id, one batch query
    (no N+1). The cause->effect side of the Calibration Log."""
    slugs = [s for s in set(change_slugs) if s]
    if not slugs:
        return {}
    rows = (
        db.query(SongRecalibration.rubric_change_slug, SongRecalibration.song_id)
        .filter(SongRecalibration.rubric_change_slug.in_(slugs))
        .filter(SongRecalibration.song_id.isnot(None))
        .all()
    )
    out: dict = {}
    for slug, song_id in rows:
        bucket = out.setdefault(slug, {})
        if song_id in bucket:
            continue
        anchor = _lookup_song_anchor(db, song_id, slug_cache)
        if anchor:
            bucket[song_id] = anchor
    return {slug: list(bucket.values()) for slug, bucket in out.items()}


def _rubric_change_to_entry(
    row: RubricChange,
    db: Session = None,
    slug_cache: dict = None,
    linked_songs: list = None,
) -> dict:
    """Adapter: rubric_changes row -> normalized feed entry.

    Instrument-level change, so there is no song_anchor and no charge/color
    before/after sides. The item text rides in rubric_change_note (reuses the
    existing renderer); public_summary is the optional voice overlay.
    linked_songs is the cause->effect list (songs this change recalibrated),
    computed by the caller via linked_songs_by_slug."""
    kind_label = _KIND_LABELS.get(row.item_kind, "Rubric")
    verb = _CHANGE_VERBS.get(row.change_type, "changed")
    headline = row.title or row.item_id
    title = headline if row.item_kind == "schema" else f"{kind_label} {verb}: {headline}"
    linked = linked_songs or []
    return {
        "event_id": row.id,
        "event_type": "rubric_change",
        "source_table": "rubric_changes",
        "pipeline": None,
        "lens": None,
        "occurred_at": row.ratified_at or row.detected_at,
        "song_anchor": None,
        "title": title,
        "before": None,
        "after": None,
        "human_rationale": None,
        "ai_rationale": None,
        "public_summary": row.public_summary,
        # The rule/tenet text itself -- shown via the existing rubric-change block.
        "rubric_change_note": row.after_text or row.before_text,
        "item_kind": row.item_kind,
        "change_type": row.change_type,
        "linked_song_count": len(linked),
        "linked_songs": linked,
        "tags": None,
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

    # Resolve a per-song filter to the unified id (callers pass the song page's
    # source/id, now 'songs'/unified-id; legacy pairs map via song_id_map).
    filter_unified_id = (
        _to_unified_id(db, song_source, song_id)
        if (song_source is not None and song_id is not None) else None
    )
    song_filter = song_source is not None and song_id is not None

    # Pre-publish corrections. (Auto-promoted 2026-04-23 — gate kept for
    # backward compat with legacy unpromoted rows, but default feed shows all.)
    if not types or "pre_publish_correction" in types:
        q = db.query(PrePublishCorrection)
        if song_filter:
            # compass_song_id now carries the unified songs.id.
            q = q.filter(PrePublishCorrection.compass_song_id == filter_unified_id) \
                if filter_unified_id else q.filter(False)
        for row in q.all():
            entries.append(_correction_to_entry(row, db, slug_cache))

    # Song recalibrations. (Auto-promoted — see note above.)
    if not types or "recalibration" in types:
        q = db.query(SongRecalibration)
        if song_filter:
            q = q.filter(SongRecalibration.song_id == filter_unified_id) \
                if filter_unified_id else q.filter(False)
        for row in q.all():
            entries.append(_recalibration_to_entry(row, db, slug_cache))

    # Rubric changes (instrument-level). No song anchor, so excluded entirely
    # from a per-song filter. (Auto-promoted like the other capture tables.)
    if (not types or "rubric_change" in types) and not song_filter:
        rc_rows = db.query(RubricChange).all()
        linked_map = linked_songs_by_slug(
            db, [r.change_slug for r in rc_rows], slug_cache
        )
        for row in rc_rows:
            entries.append(_rubric_change_to_entry(
                row, db, slug_cache, linked_songs=linked_map.get(row.change_slug),
            ))

    entries.sort(key=lambda e: e["occurred_at"] or datetime.min, reverse=True)

    total = len(entries)
    window = entries[offset:offset + limit] if limit > 0 else entries[offset:]
    return window, total
