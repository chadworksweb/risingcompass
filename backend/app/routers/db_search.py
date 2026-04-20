"""Admin DB search — read-only introspection for every RC data table.

Exposes a generic query endpoint per whitelisted table with column-aware
sort + filter. Django-style operator syntax: `col__op=value`.

Operators: eq (default), ne, gt, gte, lt, lte, contains, startswith, in, isnull.

Example:
  /api/admin/db/compass_songs?rubric_color=red&charge_value__lt=-50&year__gte=2020&sort_by=charge_value&sort_dir=asc
"""

from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import String, Text, Integer, Float, Boolean, Date, DateTime, or_, inspect as sa_inspect
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    CompassSong, LibrarySong, SubmittedSong, StreamSong,
    Artist, Release, MisreadSubmission, AudienceVibePush,
    SongRecalibration, SongReset, ReadingSong, CalibrationRun, Trash,
    SongArtist, ReleaseSong, AudienceVibeNeedle, AudienceVibeReviewCase,
    SongSlug, SongRecalibrationProposal, LcEvent,
)
from app.routers.admin import verify_admin_key
from app.services.song_search import search_unified


router = APIRouter(
    prefix="/api/admin/db",
    tags=["admin-db"],
    dependencies=[Depends(verify_admin_key)],
)


TABLES = {
    "compass_songs": CompassSong,
    "library_songs": LibrarySong,
    "submitted_songs": SubmittedSong,
    "stream_songs": StreamSong,
    "artists": Artist,
    "releases": Release,
    "misread_submissions": MisreadSubmission,
    "audience_vibe_pushes": AudienceVibePush,
    "song_recalibrations": SongRecalibration,
    "calibration_runs": CalibrationRun,
    "trash": Trash,
}

# Columns matched by the `q=` quick-search per table.
SEARCH_COLUMNS = {
    "compass_songs": ["title", "artist"],
    "library_songs": ["title", "artist"],
    "submitted_songs": ["title", "artist"],
    "stream_songs": ["title", "artist"],
    "artists": ["name", "slug"],
    "releases": ["title"],
    "misread_submissions": ["song_title", "song_artist", "email", "first_name", "last_name"],
    "audience_vibe_pushes": ["device_id", "ip_address"],
    "song_recalibrations": ["public_summary"],
    "calibration_runs": ["title", "artist"],
    "trash": ["title", "artist", "reason"],
}

OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "startswith", "in", "isnull"}

RESERVED_PARAMS = {"q", "sort_by", "sort_dir", "offset", "limit"}


def _col_type(col) -> str:
    t = col.type
    if isinstance(t, Boolean): return "boolean"
    if isinstance(t, Integer): return "integer"
    if isinstance(t, Float): return "float"
    if isinstance(t, DateTime): return "datetime"
    if isinstance(t, Date): return "date"
    if isinstance(t, (String, Text)): return "text"
    return "text"


def _coerce(col, raw: str):
    t = _col_type(col)
    if raw == "" or raw is None:
        return None
    if t == "integer":
        return int(raw)
    if t == "float":
        return float(raw)
    if t == "boolean":
        return str(raw).lower() in ("1", "true", "yes", "y")
    if t == "date":
        return date.fromisoformat(raw)
    if t == "datetime":
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return datetime.combine(date.fromisoformat(raw), datetime.min.time())
    return raw


@router.get("/tables")
def list_tables():
    out = []
    for name, model in TABLES.items():
        cols = []
        for c in sa_inspect(model).columns:
            cols.append({
                "name": c.name,
                "type": _col_type(c),
                "nullable": bool(c.nullable),
                "primary_key": bool(c.primary_key),
            })
        out.append({
            "table": name,
            "columns": cols,
            "search_columns": SEARCH_COLUMNS.get(name, []),
        })
    # Virtual all_songs — a UNION across the four song tables. Columns mirror
    # what search_unified returns so the admin DB explorer can render it with
    # the same table-driven UI.
    out.append({
        "table": "all_songs",
        "virtual": True,
        "columns": [
            {"name": "song_source", "type": "text", "nullable": False, "primary_key": False},
            {"name": "id", "type": "integer", "nullable": False, "primary_key": True},
            {"name": "title", "type": "text", "nullable": True, "primary_key": False},
            {"name": "artist", "type": "text", "nullable": True, "primary_key": False},
            {"name": "rubric_color", "type": "text", "nullable": True, "primary_key": False},
            {"name": "charge_value", "type": "integer", "nullable": True, "primary_key": False},
            {"name": "contaminated", "type": "boolean", "nullable": True, "primary_key": False},
            {"name": "contamination_note", "type": "text", "nullable": True, "primary_key": False},
            {"name": "charge_summary", "type": "text", "nullable": True, "primary_key": False},
            {"name": "slug", "type": "text", "nullable": True, "primary_key": False},
            {"name": "year", "type": "integer", "nullable": True, "primary_key": False},
            {"name": "created_at", "type": "datetime", "nullable": True, "primary_key": False},
        ],
        "search_columns": ["title", "artist"],
    })
    return out


@router.get("/{table}")
def query_table(
    table: str,
    request: Request,
    db: Session = Depends(get_db),
    q: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: str = "desc",
    offset: int = 0,
    limit: int = 25,
):
    # Virtual all_songs: UNION across the four song tables with the admin's
    # full field set (including PII). Regular filter params (col, col__op)
    # get interpreted by search_unified below.
    if table == "all_songs":
        params = request.query_params
        def _pget(k):
            v = params.get(k)
            return v if v not in (None, "") else None
        def _pget_int(k):
            v = _pget(k)
            try:
                return int(v) if v is not None else None
            except ValueError:
                return None
        result = search_unified(
            db,
            q=q,
            source=_pget("song_source"),
            tier=_pget("rubric_color"),
            charge_min=_pget_int("charge_value__gte") or _pget_int("charge_min"),
            charge_max=_pget_int("charge_value__lte") or _pget_int("charge_max"),
            year_min=_pget_int("year__gte"),
            year_max=_pget_int("year__lte"),
            contaminated=(
                True if _pget("contaminated") in ("1", "true", "yes")
                else False if _pget("contaminated") in ("0", "false", "no")
                else None
            ),
            sort_by=sort_by or "created_at",
            sort_dir=sort_dir,
            offset=max(0, offset),
            limit=max(1, min(limit, 500)),
            include_pii=True,
            attach_slugs=True,
        )
        return {
            "table": "all_songs",
            "total": result["total"],
            "offset": result["offset"],
            "limit": result["limit"],
            "rows": result["items"],
        }

    if table not in TABLES:
        raise HTTPException(404, f"Unknown table: {table}")
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    model = TABLES[table]
    col_map = {c.name: c for c in sa_inspect(model).columns}

    query = db.query(model)

    for key, raw in request.query_params.multi_items():
        if key in RESERVED_PARAMS:
            continue
        if "__" in key:
            col_name, _, op = key.rpartition("__")
            if op not in OPERATORS:
                col_name, op = key, "eq"
        else:
            col_name, op = key, "eq"
        if col_name not in col_map:
            continue
        col = col_map[col_name]

        if op == "isnull":
            is_null = str(raw).lower() in ("1", "true", "yes", "y")
            query = query.filter(col.is_(None) if is_null else col.isnot(None))
            continue

        if op == "in":
            parts = [p.strip() for p in str(raw).split(",") if p.strip()]
            coerced = []
            for p in parts:
                try:
                    coerced.append(_coerce(col, p))
                except (ValueError, TypeError):
                    pass
            if coerced:
                query = query.filter(col.in_(coerced))
            continue

        try:
            val = _coerce(col, raw)
        except (ValueError, TypeError):
            continue
        if val is None:
            continue

        if op == "eq":
            query = query.filter(col == val)
        elif op == "ne":
            query = query.filter(col != val)
        elif op == "gt":
            query = query.filter(col > val)
        elif op == "gte":
            query = query.filter(col >= val)
        elif op == "lt":
            query = query.filter(col < val)
        elif op == "lte":
            query = query.filter(col <= val)
        elif op == "contains":
            query = query.filter(col.ilike(f"%{val}%"))
        elif op == "startswith":
            query = query.filter(col.ilike(f"{val}%"))

    if q:
        search_cols = [col_map[c] for c in SEARCH_COLUMNS.get(table, []) if c in col_map]
        if search_cols:
            query = query.filter(or_(*[sc.ilike(f"%{q}%") for sc in search_cols]))

    total = query.count()

    if sort_by and sort_by in col_map:
        col = col_map[sort_by]
        query = query.order_by(col.desc() if sort_dir == "desc" else col.asc())
    elif "id" in col_map:
        query = query.order_by(col_map["id"].desc())

    rows = query.offset(offset).limit(limit).all()

    def serialize(row):
        data = {}
        for name, col in col_map.items():
            v = getattr(row, name, None)
            if isinstance(v, (datetime, date)):
                v = v.isoformat()
            data[name] = v
        return data

    return {
        "table": table,
        "total": total,
        "offset": offset,
        "limit": limit,
        "rows": [serialize(r) for r in rows],
    }


# --- Song reset ---

RESETTABLE_SOURCES = {
    "compass_songs": (CompassSong, "compass"),
    "library_songs": (LibrarySong, "library"),
    "submitted_songs": (SubmittedSong, "submitted"),
    "stream_songs": (StreamSong, "stream"),
}


class SongResetIn(BaseModel):
    reason: str = Field(..., min_length=4, max_length=2000)


@router.get("/{table}/{song_id}/reset-preview")
def reset_preview(table: str, song_id: int, db: Session = Depends(get_db)):
    """Preflight — current classification + any loud consequences of a reset.

    For compass songs, surfaces which daily readings currently cite this song
    (resetting nulls their cited charge). Admin sees this before confirming.
    """
    if table not in RESETTABLE_SOURCES:
        raise HTTPException(400, f"Reset not supported on {table}")
    Model, polymorphic = RESETTABLE_SOURCES[table]
    row = db.query(Model).get(song_id)
    if not row:
        raise HTTPException(404, f"{table} id={song_id} not found")
    warnings = []
    if polymorphic == "compass":
        reading_rows = (
            db.query(ReadingSong.reading_id, ReadingSong.position)
            .filter(ReadingSong.compass_song_id == song_id)
            .all()
        )
        if reading_rows:
            warnings.append({
                "kind": "reading_references",
                "count": len(reading_rows),
                "detail": "This song is cited in one or more daily readings. Resetting leaves those reading_songs rows pointing at the now-uncalibrated song — the reading's cited tier/charge for this slot will be blank until a re-calibration.",
            })
    if getattr(row, "charge_value", None) is None:
        warnings.append({
            "kind": "already_null",
            "detail": "This song is already uncalibrated — reset will still log an audit entry but nothing else changes.",
        })
    return {
        "song_source": polymorphic,
        "song_id": song_id,
        "title": getattr(row, "title", None),
        "artist": getattr(row, "artist", None),
        "current": {
            "charge_value": getattr(row, "charge_value", None),
            "rubric_color": getattr(row, "rubric_color", None),
            "charge_summary": getattr(row, "charge_summary", None),
            "contaminated": getattr(row, "contaminated", None),
            "contamination_note": getattr(row, "contamination_note", None),
        },
        "warnings": warnings,
    }


@router.post("/{table}/{song_id}/reset")
def reset_song(table: str, song_id: int, data: SongResetIn, db: Session = Depends(get_db)):
    """Return a song to uncalibrated state. Nulls classification fields on the
    row; writes a SongReset audit entry with the full before-snapshot. Keeps
    id, title, artist, and every junction-table linkage intact.
    """
    if table not in RESETTABLE_SOURCES:
        raise HTTPException(400, f"Reset not supported on {table}")
    Model, polymorphic = RESETTABLE_SOURCES[table]
    row = db.query(Model).get(song_id)
    if not row:
        raise HTTPException(404, f"{table} id={song_id} not found")

    reset_row = SongReset(
        song_source=polymorphic,
        song_id=song_id,
        before_charge=getattr(row, "charge_value", None),
        before_color=getattr(row, "rubric_color", None),
        before_summary=getattr(row, "charge_summary", None),
        before_contaminated=getattr(row, "contaminated", None),
        before_contamination_note=getattr(row, "contamination_note", None),
        reason=data.reason.strip(),
    )
    db.add(reset_row)

    # Null the classification fields. rubric_color is NOT NULL on the three
    # primary song tables, so use "" as the uncalibrated sentinel — public
    # renderers key off charge_value IS NULL regardless.
    if hasattr(row, "charge_value"): row.charge_value = None
    if hasattr(row, "charge_summary"): row.charge_summary = None
    if hasattr(row, "contaminated"): row.contaminated = False
    if hasattr(row, "contamination_note"): row.contamination_note = None
    if hasattr(row, "rubric_color"): row.rubric_color = ""
    db.commit()
    db.refresh(reset_row)

    return {
        "reset_id": reset_row.id,
        "song_source": polymorphic,
        "song_id": song_id,
        "reset_at": reset_row.reset_at.isoformat(),
    }


# --- Soft delete (move to trash) ---

# Tables that support soft-delete via the admin DB explorer. Songs always
# route through their polymorphic source; other tables go to trash by name.
DELETABLE_TABLES = set(TABLES.keys()) - {"trash"}


class DeleteIn(BaseModel):
    reason: str = Field(..., min_length=4, max_length=2000)


def _serialize_row(row, col_map) -> dict:
    out = {}
    for name in col_map:
        v = getattr(row, name, None)
        if isinstance(v, (datetime, date)):
            v = v.isoformat()
        out[name] = v
    return out


# Every table that holds a polymorphic (song_source, song_id) pointer.
# On song delete, matching rows are snapshotted into trash AND deleted so
# the song becomes fully invisible outside the trash entry.
_POLYMORPHIC_SONG_REFS = [
    ("song_artists", SongArtist),
    ("release_songs", ReleaseSong),
    ("calibration_runs", CalibrationRun),
    ("audience_vibe_needles", AudienceVibeNeedle),
    ("audience_vibe_pushes", AudienceVibePush),
    ("audience_vibe_review_cases", AudienceVibeReviewCase),
    ("song_slugs", SongSlug),
    ("song_recalibrations", SongRecalibration),
    ("song_recalibration_proposals", SongRecalibrationProposal),
    ("song_resets", SongReset),
    ("misread_submissions", MisreadSubmission),
]


def _capture_and_delete_song_cascade(polymorphic: str, song_id: int, db: Session) -> dict:
    """Snapshot + delete every row anywhere that references this song.

    Polymorphic (song_source, song_id) references and the two FK-based
    references (reading_songs.compass_song_id, lc_events.submission_id)
    are all captured in related_data and removed from their tables, so
    the song is invisible outside the trash entry.
    """
    related: dict = {}

    for name, Model in _POLYMORPHIC_SONG_REFS:
        col_map = {c.name: c for c in sa_inspect(Model).columns}
        rows = (
            db.query(Model)
            .filter(Model.song_source == polymorphic)
            .filter(Model.song_id == song_id)
            .all()
        )
        if rows:
            related[name] = [_serialize_row(r, col_map) for r in rows]
            for r in rows:
                db.delete(r)

    # Compass-specific FK ref
    if polymorphic == "compass":
        col_map = {c.name: c for c in sa_inspect(ReadingSong).columns}
        rs_rows = (
            db.query(ReadingSong)
            .filter(ReadingSong.compass_song_id == song_id)
            .all()
        )
        if rs_rows:
            related["reading_songs"] = [_serialize_row(r, col_map) for r in rs_rows]
            for r in rs_rows:
                db.delete(r)

    # Submitted-specific FK ref
    if polymorphic == "submitted":
        col_map = {c.name: c for c in sa_inspect(LcEvent).columns}
        lc_rows = (
            db.query(LcEvent)
            .filter(LcEvent.submission_id == song_id)
            .all()
        )
        if lc_rows:
            related["lc_events"] = [_serialize_row(r, col_map) for r in lc_rows]
            for r in lc_rows:
                db.delete(r)

    return related


@router.post("/{table}/{row_id}/delete")
def delete_row(table: str, row_id: int, data: DeleteIn, db: Session = Depends(get_db)):
    """Soft-delete: snapshot the row (and relevant junctions) into trash,
    then remove the original. Polymorphic references elsewhere (song_slugs,
    misread_submissions, audience_vibe_*) become dangling but harmless —
    queries that resolve (source, id) just return nothing.
    """
    import json as _json

    if table not in DELETABLE_TABLES:
        raise HTTPException(400, f"Delete not supported on {table}")
    Model = TABLES[table]
    row = db.query(Model).get(row_id)
    if not row:
        raise HTTPException(404, f"{table} id={row_id} not found")

    col_map = {c.name: c for c in sa_inspect(Model).columns}
    row_snapshot = _serialize_row(row, col_map)

    # Song tables get full cascade so the song stops touching anything
    # except the trash entry. Other tables: simple delete, no cascade.
    related: dict = {}
    if table in RESETTABLE_SOURCES:
        polymorphic = RESETTABLE_SOURCES[table][1]
        related = _capture_and_delete_song_cascade(polymorphic, row_id, db)

    trash_row = Trash(
        original_table=table,
        original_id=row_id,
        title=getattr(row, "title", None) or getattr(row, "name", None),
        artist=getattr(row, "artist", None),
        row_data=_json.dumps(row_snapshot, default=str),
        related_data=_json.dumps(related, default=str) if related else None,
        reason=data.reason.strip(),
    )
    db.add(trash_row)
    db.delete(row)
    db.commit()
    db.refresh(trash_row)

    return {
        "trash_id": trash_row.id,
        "original_table": table,
        "original_id": row_id,
        "deleted_at": trash_row.deleted_at.isoformat(),
    }
