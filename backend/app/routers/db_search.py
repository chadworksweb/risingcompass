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
from sqlalchemy import String, Text, Integer, Float, Boolean, Date, DateTime, or_, inspect as sa_inspect
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    CompassSong, LibrarySong, SubmittedSong, StreamSong,
    Artist, Release, MisreadSubmission, AudienceVibePush,
    SongRecalibration,
)
from app.routers.admin import verify_admin_key


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
