"""Native storage chokepoint for the unified song entity.

Phase 5b: every calibrated-song writer (compass daily, Lyrical Charger, Album
Charger, CL Stream, library/manual admin, backfill) lands its row here via
`store_calibrated_song` -> `upsert_unified_song`, writing ONLY the unified model
(`songs` + `chart_appearances` + `song_ingestions`) keyed by canonical_key. No
legacy song-table read or write. The Phase-3 dual-write mirror
(sync_legacy_song_to_unified / safe_sync) is retired.

Idempotent (upsert by canonical_key). Authoritative-first: a crowd write
(lyrical_charger/stream) never overwrites an existing authoritative
(chart_reading/catalog_backfill/terminal) calibration on the unified row. NEVER touches
prose_provenance_anchors. See RISING-COMPASS-SONG-ENTITY-RENOVATION.md.
"""

import json
import logging

from sqlalchemy import text

from app.services.song_identity import (
    compute_canonical_key, compute_canonical_key_clean, resolve_song_identity,
)
from app.services.artist_utils import generate_song_slug
from app.constants import CHART_SOURCE_TO_CHART_SLUG

logger = logging.getLogger(__name__)

_AUTH_SOURCES = {"compass", "library"}
_AUTH_METHODS = {"chart_reading", "catalog_backfill", "terminal"}
_METHOD = {
    "compass": "chart_reading", "library": "catalog_backfill",
    "submitted": "lyrical_charger", "stream": "stream",
}
# Calibration columns copied onto the unified songs row (absent ones -> NULL).
_CALIB = [
    "rubric_color", "charge_value", "charge_summary", "contaminated",
    "contamination_note", "dogma_referenced", "dogma_note", "instrumental",
    "lyrics_unavailable", "translated", "medley", "preorder",
    "confidence", "listener_effects_prose", "societal_effects_prose",
    "societal_prose_generated_at", "societal_prose_model", "prior_listener_effects_prose",
    "prior_societal_effects_prose", "prior_societal_prose_generated_at",
    "prior_societal_prose_model", "deadpan_line", "topics", "topic_audit",
    "activations", "calibration_failed", "message_analysis",
    "expression_analysis", "intention_analysis",
]


def upsert_unified_song(db, source: str, legacy_id, row: dict, *, ingestion_detail: dict | None = None,
                        only_set_present: bool = False):
    """Upsert one song into songs + chart_appearance + song_ingestion and repoint
    its references, driven entirely by the in-memory `row` dict -- NO legacy-table
    read, so it survives the Phase-5 drop. `row` carries the identity + calibration
    fields (the legacy column names: title, artist, the _CALIB set, plus
    chart_source/year/chart_position/chart_position_letter/album_id/track_number/
    ip_address/source as applicable). `legacy_id` is used only to maintain
    song_id_map + repoint (song_source, legacy_id) references during the
    transition; pass None once the legacy tables are gone. Does NOT commit --
    the caller owns the transaction. Returns the unified songs.id or None.

    Authoritative-first: a crowd write (lyrical_charger/stream) never overwrites
    an existing authoritative (chart_reading/editorial/terminal) calibration."""
    title, artist = row.get("title"), row.get("artist")
    if not title or not artist:
        return None
    # Feeder-cruft guard: the social-discovery feeds (source 'compass' =
    # chart_reading: YouTube Trending, Shazam, iTunes, ...) carry raw upload
    # strings ("ARTIST - Title (Official Music Video)" / "ARTISTVEVO"). Clean the
    # DISPLAY title/artist here, at the sole songs INSERT, so the stored row +
    # canonical_key + slug are clean instead of minting a cruft duplicate. No-op
    # on already-clean strings and on normal "(feat. X)" titles (gated by
    # is_feeder_upload). The clean key already dedups; this fixes the display.
    if source == "compass":
        from app.services.feeder_clean import clean_feeder_display
        title, artist = clean_feeder_display(title, artist)
    key = compute_canonical_key(title, artist)
    clean_key = compute_canonical_key_clean(title, artist)
    method = _METHOD[source]
    incoming_auth = source in _AUTH_SOURCES

    # Identity-resolution ladder: exact canonical_key (fast path), then the
    # cleaned key (catches feeder formatting drift -- MV cruft, VEVO/label
    # artists). A clean-rung hit lands the write on the EXISTING row instead of
    # minting a duplicate. Pure read; the actual write is below.
    resolution = resolve_song_identity(db, title, artist)
    existing = None
    if resolution.song_id:
        existing = db.execute(
            text("SELECT id, canonical_calibration_method, canonical_key_clean "
                 "FROM songs WHERE id = :id"),
            {"id": resolution.song_id},
        ).mappings().first()

    if existing:
        song_id = existing["id"]
        cur_auth = (existing["canonical_calibration_method"] or "") in _AUTH_METHODS
        # Opportunistically stamp the clean key on a row that predates the
        # backfill (or was matched via the raw-key clean rung), so future
        # resolutions hit it directly on the indexed clean column.
        if not existing.get("canonical_key_clean"):
            db.execute(
                text("UPDATE songs SET canonical_key_clean = :ck WHERE id = :id"),
                {"ck": clean_key, "id": song_id},
            )
        # Authoritative-first: only overwrite calibration when the incoming write
        # is authoritative, or the existing row isn't authoritative.
        if incoming_auth or not cur_auth:
            # only_set_present (native writes): overwrite only the calibration
            # columns the incoming object actually carries, so a re-read that
            # omits prose/analysis fields never nulls existing values. The mirror
            # path passes the full legacy row, so default (full overwrite) holds.
            cols = [c for c in _CALIB if row.get(c) is not None] if only_set_present else _CALIB
            if cols:
                sets = ", ".join(f"{c} = :{c}" for c in cols)
                params = {c: row.get(c) for c in cols}
                params.update({"sid": song_id, "m": method})
                db.execute(text(
                    f"UPDATE songs SET {sets}, canonical_calibration_method = :m WHERE id = :sid"
                ), params)
    else:
        params = {c: row.get(c) for c in _CALIB}
        # The boolean flag columns are NOT NULL (server default False). A raw-SQL
        # INSERT bypasses the ORM `default=False`, so a minimal/partial row (e.g.
        # the lyrics_unavailable disposition write, which carries no calibration)
        # would send None and trip the NOT-NULL constraint. Coerce None -> False
        # for these; fully-specified calibration callers are unaffected.
        for _b in ("contaminated", "dogma_referenced", "instrumental",
                   "lyrics_unavailable", "translated", "medley", "preorder",
                   "calibration_failed"):
            if params.get(_b) is None:
                params[_b] = False
        params.update({
            "title": title, "artist": artist, "canonical_key": key,
            "canonical_key_clean": clean_key, "m": method,
            "album_id": row.get("album_id"), "track_number": row.get("track_number"),
        })
        collist = "title, artist, canonical_key, canonical_key_clean, canonical_calibration_method, album_id, track_number, created_at, " + ", ".join(_CALIB)
        vallist = ":title, :artist, :canonical_key, :canonical_key_clean, :m, :album_id, :track_number, (now() at time zone 'utc'), " + ", ".join(f":{c}" for c in _CALIB)
        song_id = db.execute(
            text(f"INSERT INTO songs ({collist}) VALUES ({vallist}) RETURNING id"), params
        ).scalar()

        # Phase 2: the identity ladder's fuzzy (trgm) rung flagged this new row as
        # a gray-band near-match of existing song(s). Queue each as a merge
        # CANDIDATE for human review -- never auto-merged. Dormant unless
        # identity_trgm.enabled (resolution.candidates is empty otherwise).
        if getattr(resolution, "candidates", None):
            from app.config import settings as _settings
            for cand_id in resolution.candidates:
                lo, hi = sorted((song_id, cand_id))
                exists = db.execute(text(
                    "SELECT 1 FROM song_merge_candidates "
                    "WHERE source_song_id = :lo AND target_song_id = :hi AND status = 'open' LIMIT 1"
                ), {"lo": lo, "hi": hi}).scalar()
                if not exists:
                    db.execute(text(
                        "INSERT INTO song_merge_candidates "
                        "(source_song_id, target_song_id, reason, detected_by, status, environment) "
                        "VALUES (:lo, :hi, 'trgm', 'resolve_ladder', 'open', :env)"
                    ), {"lo": lo, "hi": hi, "env": _settings.environment})

    # id map (idempotent) -- transition-only; needs the legacy (source, id).
    if legacy_id:
        db.execute(text(
            "INSERT INTO song_id_map (old_source, old_id, new_song_id, canonical_key) "
            "VALUES (:s, :i, :n, :k) "
            "ON CONFLICT (old_source, old_id) DO UPDATE SET new_song_id = :n, canonical_key = :k"
        ), {"s": source, "i": legacy_id, "n": song_id, "k": key})

    # chart appearance (compass only; non-chart sources get none)
    if source == "compass":
        slug = CHART_SOURCE_TO_CHART_SLUG.get(row.get("chart_source") or "billboard_hot_100")
        if slug:
            cid = db.execute(text("SELECT id FROM charts WHERE slug = :s"), {"s": slug}).scalar()
            if cid:
                db.execute(text(
                    "INSERT INTO chart_appearances (song_id, chart_id, year, position, position_letter) "
                    "VALUES (:sid, :cid, :y, :p, :pl) "
                    "ON CONFLICT (song_id, chart_id, year, position, position_letter) DO NOTHING"
                ), {"sid": song_id, "cid": cid, "y": row.get("year"),
                    "p": row.get("chart_position"), "pl": row.get("chart_position_letter") or ""})

    # ingestion (one per song+method)
    if not db.execute(text(
        "SELECT 1 FROM song_ingestions WHERE song_id = :s AND method = :m LIMIT 1"
    ), {"s": song_id, "m": method}).scalar():
        if ingestion_detail is not None:
            detail = ingestion_detail
        else:
            detail = {"chart_source": row.get("chart_source")} if source == "compass" else {"source": row.get("source")}
        db.execute(text(
            "INSERT INTO song_ingestions (song_id, method, ip_address, detail) "
            "VALUES (:s, :m, :ip, :d)"
        ), {"s": song_id, "m": method, "ip": row.get("ip_address"), "d": json.dumps(detail)})

    # Stamp origin_chart on the FIRST chart appearance (immutable). The
    # `origin_chart IS NULL` guard means a later chart appearance never
    # overwrites the first one. Build 7 -- the gutter-vs-mainstream origin signal.
    if method == "chart_reading" and row.get("chart_source"):
        db.execute(text(
            "UPDATE songs SET origin_chart = :c WHERE id = :s AND origin_chart IS NULL"
        ), {"c": row.get("chart_source"), "s": song_id})

    # Persist a canonical URL slug for EVERY song at the write chokepoint, so no
    # ingestion path (chart_reading was the historical gap -- 415 slugless songs)
    # can leave a song without a stable public URL or out of the sitemap.
    ensure_song_slug(db, song_id, title, artist)

    return song_id


def ensure_song_slug(db, song_id: int, title: str, artist: str) -> str | None:
    """Idempotently guarantee a `song_slugs` row for `song_id`. Returns the slug.
    Collision-safe (appends -2, -3, ... like the read-path minter). Does NOT
    commit -- the caller owns the transaction (matches upsert_unified_song)."""
    if not song_id:
        return None
    existing = db.execute(
        text("SELECT slug FROM song_slugs WHERE song_id = :s ORDER BY id LIMIT 1"),
        {"s": song_id},
    ).scalar()
    if existing:
        return existing
    base = generate_song_slug(title, artist)
    slug = base
    suffix = 2
    while db.execute(text("SELECT 1 FROM song_slugs WHERE slug = :sl LIMIT 1"),
                     {"sl": slug}).scalar():
        slug = f"{base}-{suffix}"
        suffix += 1
    db.execute(text(
        "INSERT INTO song_slugs (slug, title, artist, song_id) "
        "VALUES (:sl, :t, :a, :s) ON CONFLICT (slug) DO NOTHING"
    ), {"sl": slug, "t": title, "a": artist, "s": song_id})
    return slug


def record_chart_ingestion(db, song_id: int, chart_source: str | None) -> None:
    """Log that a song surfaced on a chart (method='chart_reading') and stamp
    songs.origin_chart on its FIRST chart appearance (immutable).

    For the cache-hit chart path (compass_agent.run_compass_agent), which reuses
    a stored calibration and so bypasses the store_calibrated_song chokepoint.
    Without this, a song already in the Library that first surfaces on Shazam /
    YouTube would leave NO chart_reading ingestion + no origin, making the
    gutter-migration signal invisible exactly when it matters (Build 7).
    Idempotent (deduped on the existing chart_reading row + the IS NULL guard);
    does NOT commit -- the caller owns the transaction."""
    if not song_id:
        return
    has_chart = db.execute(text(
        "SELECT 1 FROM song_ingestions WHERE song_id = :s AND method = 'chart_reading' LIMIT 1"
    ), {"s": song_id}).scalar()
    if not has_chart:
        db.execute(text(
            "INSERT INTO song_ingestions (song_id, method, detail) "
            "VALUES (:s, 'chart_reading', :d)"
        ), {"s": song_id, "d": json.dumps({"chart_source": chart_source})})
    if chart_source:
        db.execute(text(
            "UPDATE songs SET origin_chart = :c WHERE id = :s AND origin_chart IS NULL"
        ), {"c": chart_source, "s": song_id})


# --- native storage chokepoint ------------------------------------------- #

# Calibration-object columns that pass straight onto the songs row. topics /
# topic_audit are JSON-encoded separately (Text columns). Mirrors the legacy
# analyzer._song_persist_fields so every native writer maps identically.
_PASSTHROUGH = [
    "rubric_color", "charge_value", "charge_summary", "contamination_note",
    "dogma_note", "confidence", "listener_effects_prose", "societal_effects_prose",
    "societal_prose_generated_at", "societal_prose_model", "deadpan_line",
    "activations", "message_analysis", "expression_analysis", "intention_analysis",
]


def calibration_to_columns(calibration: dict) -> dict:
    """Map a calibration object to the songs calibration columns (JSON-encoding
    topics / topic_audit to match the Text columns)."""
    topics = calibration.get("topics")
    topic_audit = calibration.get("topic_audit")
    out = {k: calibration.get(k) for k in _PASSTHROUGH}
    out["contaminated"] = bool(calibration.get("contaminated", False))
    out["dogma_referenced"] = bool(calibration.get("dogma_referenced", False))
    out["instrumental"] = bool(calibration.get("instrumental", False))
    out["translated"] = bool(calibration.get("translated", False))
    out["medley"] = bool(calibration.get("medley", False))
    out["preorder"] = bool(calibration.get("preorder", False))
    out["calibration_failed"] = bool(calibration.get("calibration_failed", False))
    out["topics"] = json.dumps(topics) if topics else None
    out["topic_audit"] = json.dumps(topic_audit) if topic_audit else None
    return out


def store_calibrated_song(
    db, *, source: str, title: str, artist: str, calibration: dict,
    chart_source: str | None = None, year: int | None = None,
    chart_position: int | None = None, chart_position_letter: str = "",
    album_id: int | None = None, track_number: int | None = None,
    ip_address: str | None = None,
    ingestion_detail: dict | None = None,
    artist_entries: list | None = None,
) -> tuple[int | None, bool]:
    """Native storage chokepoint -- the single place a calibrated song lands in
    the unified model. Upserts the songs row by canonical_key (authoritative-
    first via `source`/method), writes a chart_appearance (chart sources only)
    and a song_ingestion (with optional workflow `ingestion_detail`), and links
    artists onto the unified id. NO legacy-table touch. Returns (songs.id,
    created) -- created=True when this call inserted a brand-new songs row.
    Returns (None, False) if the calibration failed (no rubric_color). Does NOT
    commit."""
    if calibration.get("rubric_color") is None:
        return None, False
    # Feeder-cruft guard (mirrors upsert_unified_song): for chart feeds, clean the
    # display title/artist AND re-derive the artist credits from the cleaned
    # artist, so the song_artists link points at the real performer ("Olivia
    # Rodrigo") instead of the upload channel ("OliviaRodrigoVEVO"). Done here so
    # the `existed` resolution + the link below both use the clean identity. No-op
    # on clean strings (is_feeder_upload gate); non-feeder sources untouched.
    if source == "compass":
        from app.services.feeder_clean import clean_feeder_display
        ct, ca = clean_feeder_display(title, artist)
        if (ct, ca) != (title, artist):
            title, artist = ct, ca
            from app.services.artist_linker import parse_artist_string
            artist_entries = parse_artist_string(artist or "")
    # charge_summary absence/verdict-framing guard at the write chokepoint -- the
    # last net before persistence. Covers every writer (calibrator API path,
    # terminal-supplied backfill, Album Charger, library/manual admin). Non-
    # blocking: a false positive must never block a legit write, but real drift
    # gets a loud, operator-visible log. See summary_guard.py.
    from app.services.agents.summary_guard import summary_has_absence_framing
    if summary_has_absence_framing(calibration.get("charge_summary")):
        logger.warning(
            "store_calibrated_song: charge_summary carries absence/verdict framing "
            "for '%s' by %s (source=%s): %r",
            title, artist, source, calibration.get("charge_summary"),
        )
    # `created` reflects the identity ladder, not just the exact key: a feeder
    # re-entry that resolves to an existing row via the clean key is NOT new, so
    # it must not re-log a calibration run / re-emit a "new song" signal.
    existed = resolve_song_identity(db, title, artist).song_id is not None
    row = calibration_to_columns(calibration)
    row.update({
        "title": title, "artist": artist,
        "chart_source": chart_source, "year": year,
        "chart_position": chart_position,
        "chart_position_letter": chart_position_letter or "",
        "album_id": album_id, "track_number": track_number,
        "ip_address": ip_address,
    })
    song_id = upsert_unified_song(db, source, None, row, ingestion_detail=ingestion_detail,
                                  only_set_present=True)
    if song_id and artist_entries:
        from app.services.artist_linker import link_song_artists
        link_song_artists(db, song_id=song_id, entries=artist_entries)
    return song_id, (not existed)
