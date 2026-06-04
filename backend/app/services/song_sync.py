"""Dual-write sync -- mirror one legacy song row into the unified model.

Phase-3 transition mechanism. The legacy writers (compass_agent._store_calibration,
analyzer Lyrical Charger, stream, draft approval) stay the CANONICAL calibration
path; right after each legacy write they call sync_legacy_song_to_unified so the
unified `songs` + `chart_appearances` stay fresh and the repointed references
(reading_songs.song_id, song_artists.unified_song_id, ...) keep pointing at the
right entity. This lets the read paths flip to the unified model incrementally
WITHOUT staleness, instead of a single big-bang cutover. Removed at Phase 5 when
the legacy writers are retired and the legacy tables drop.

Idempotent (upsert by canonical_key) and best-effort (callers wrap in try/except).
Authoritative-first: a crowd write (lyrical_charger/stream) never overwrites an
existing authoritative (chart_reading/editorial) calibration on the unified row.
NEVER touches prose_provenance_anchors. See RISING-COMPASS-SONG-ENTITY-RENOVATION.md.
"""

import json
import logging

from sqlalchemy import text

from app.services.song_identity import compute_canonical_key
from app.constants import CHART_SOURCE_TO_CHART_SLUG

logger = logging.getLogger(__name__)

_AUTH_SOURCES = {"compass", "library"}
_AUTH_METHODS = {"chart_reading", "editorial", "terminal"}
_METHOD = {
    "compass": "chart_reading", "library": "editorial",
    "submitted": "lyrical_charger", "stream": "stream",
}
_LEGACY_TABLE = {
    "compass": "compass_songs", "library": "library_songs",
    "submitted": "submitted_songs", "stream": "cl_stream_songs",
}
# Calibration columns copied onto the unified songs row (absent ones -> NULL).
_CALIB = [
    "rubric_color", "charge_value", "charge_summary", "contaminated",
    "contamination_note", "dogma_referenced", "dogma_note", "instrumental",
    "confidence", "effects_prose", "societal_effects_prose",
    "societal_prose_generated_at", "societal_prose_model", "prior_effects_prose",
    "prior_societal_effects_prose", "prior_societal_prose_generated_at",
    "prior_societal_prose_model", "deadpan_line", "topics", "topic_audit",
    "activations", "calibration_failed", "message_analysis",
    "expression_analysis", "intention_analysis",
]


def sync_legacy_song_to_unified(db, source: str, legacy_id: int):
    """Mirror legacy (source, legacy_id) into the unified model by READING the
    legacy row, then delegating to upsert_unified_song. Phase-3 dual-write path;
    retired at Phase 5 once every writer calls upsert_unified_song natively (no
    legacy read). Does NOT commit -- the caller owns the transaction."""
    tbl = _LEGACY_TABLE.get(source)
    if not tbl or not legacy_id:
        return None
    row = db.execute(text(f"SELECT * FROM {tbl} WHERE id = :i"), {"i": legacy_id}).mappings().first()
    if not row:
        return None
    return upsert_unified_song(db, source, legacy_id, dict(row))


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
    key = compute_canonical_key(title, artist)
    method = _METHOD[source]
    incoming_auth = source in _AUTH_SOURCES

    existing = db.execute(
        text("SELECT id, canonical_calibration_method FROM songs WHERE canonical_key = :k"),
        {"k": key},
    ).mappings().first()

    if existing:
        song_id = existing["id"]
        cur_auth = (existing["canonical_calibration_method"] or "") in _AUTH_METHODS
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
        params.update({
            "title": title, "artist": artist, "canonical_key": key, "m": method,
            "album_id": row.get("album_id"), "track_number": row.get("track_number"),
        })
        collist = "title, artist, canonical_key, canonical_calibration_method, album_id, track_number, " + ", ".join(_CALIB)
        vallist = ":title, :artist, :canonical_key, :m, :album_id, :track_number, " + ", ".join(f":{c}" for c in _CALIB)
        song_id = db.execute(
            text(f"INSERT INTO songs ({collist}) VALUES ({vallist}) RETURNING id"), params
        ).scalar()

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

    # repoint this song's references -- transition-only; keyed by the legacy
    # (source, id). Post-drop the references already carry the unified song_id
    # (renamed from unified_song_id in Phase 5c), so this no-ops out.
    if legacy_id:
        for t in ("song_artists", "song_slugs", "release_songs", "user_calibrations",
                  "calibration_runs", "misread_submissions"):
            db.execute(text(
                f"UPDATE {t} SET unified_song_id = :n WHERE song_source = :s AND song_id = :i"
            ), {"n": song_id, "s": source, "i": legacy_id})
        if source == "compass":
            db.execute(text("UPDATE reading_songs SET song_id = :n WHERE compass_song_id = :i"),
                       {"n": song_id, "i": legacy_id})
            db.execute(text("UPDATE agent_draft_songs SET song_id = :n WHERE compass_song_id = :i"),
                       {"n": song_id, "i": legacy_id})
    return song_id


def safe_sync(db, source: str, legacy_id: int):
    """Best-effort wrapper: sync + commit, swallow + log failures so the
    canonical legacy write is never jeopardised by the dual-write mirror."""
    try:
        sid = sync_legacy_song_to_unified(db, source, legacy_id)
        db.commit()
        return sid
    except Exception:
        logger.exception("song_sync failed for %s:%s", source, legacy_id)
        try:
            db.rollback()
        except Exception:
            pass
        return None


# --- native storage chokepoint ------------------------------------------- #

# Calibration-object columns that pass straight onto the songs row. topics /
# topic_audit are JSON-encoded separately (Text columns). Mirrors the legacy
# analyzer._song_persist_fields so every native writer maps identically.
_PASSTHROUGH = [
    "rubric_color", "charge_value", "charge_summary", "contamination_note",
    "dogma_note", "confidence", "effects_prose", "societal_effects_prose",
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
    out["calibration_failed"] = bool(calibration.get("calibration_failed", False))
    out["topics"] = json.dumps(topics) if topics else None
    out["topic_audit"] = json.dumps(topic_audit) if topic_audit else None
    return out


def store_calibrated_song(
    db, *, source: str, title: str, artist: str, calibration: dict,
    chart_source: str | None = None, year: int | None = None,
    chart_position: int | None = None, chart_position_letter: str = "",
    album_id: int | None = None, track_number: int | None = None,
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
    key = compute_canonical_key(title, artist)
    existed = db.execute(
        text("SELECT 1 FROM songs WHERE canonical_key = :k LIMIT 1"), {"k": key}
    ).scalar()
    row = calibration_to_columns(calibration)
    row.update({
        "title": title, "artist": artist,
        "chart_source": chart_source, "year": year,
        "chart_position": chart_position,
        "chart_position_letter": chart_position_letter or "",
        "album_id": album_id, "track_number": track_number,
    })
    song_id = upsert_unified_song(db, source, None, row, ingestion_detail=ingestion_detail,
                                  only_set_present=True)
    if song_id and artist_entries:
        from app.services.artist_linker import link_song_artists
        link_song_artists(
            db, song_source="songs", song_id=song_id,
            unified_song_id=song_id, entries=artist_entries,
        )
    return song_id, (not existed)
