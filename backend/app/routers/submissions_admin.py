"""Admin endpoints for Lyrical Charger submissions -- viewer, stats, promotion.

Unified song-entity renovation (Phase 5b): native. A "submitted song" is no
longer a row in `submitted_songs`. A Lyrical Charger submission is an atomic
`songs` row that carries a `song_ingestions` row with method='lyrical_charger';
the legacy `submitted_songs.source` workflow field now lives in that ingestion's
`detail` JSON (`source`), and `submitted_songs.ip_address` lives in
`song_ingestions.ip_address`. The admin responses are rebuilt from songs + the
lyrical_charger ingestion. Promotion no longer copies a row into a second table
-- the song already IS in the Library; it only elevates the canonical
calibration to authoritative editorial (so a later crowd read can't override it)
and records an editorial breadcrumb.
"""

import json
from datetime import datetime, date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import SubmittedSongOut, SubmissionStatsOut
from app.routers.admin import verify_admin_key

router = APIRouter(prefix="/api/admin/submissions", tags=["submissions-admin"])


# --- response rebuild (songs + lyrical_charger ingestion) ---

# One submission = one song_ingestions row (method='lyrical_charger') joined to
# its songs row. `id` is the songs.id (stable per song; delete/promote resolve
# by it). The `source` workflow field + ip_address come off the ingestion; the
# calibration off songs. The DISTINCT ON keeps one row per song even if a song
# somehow carries more than one lyrical_charger ingestion (latest wins).
_SUB_SELECT = """
    SELECT DISTINCT ON (s.id)
           s.id AS id, s.title AS title, s.artist AS artist,
           s.rubric_color AS rubric_color, s.charge_value AS charge_value,
           s.contaminated AS contaminated, s.contamination_note AS contamination_note,
           s.dogma_referenced AS dogma_referenced, s.dogma_note AS dogma_note,
           s.charge_summary AS charge_summary, s.confidence AS confidence,
           s.created_at AS submitted_at,
           i.ip_address AS ip_address, i.detail AS detail
    FROM songs s
    JOIN song_ingestions i ON i.song_id = s.id AND i.method = 'lyrical_charger'
"""


def _sub_out(row) -> dict:
    """Build a SubmittedSongOut-shaped dict from a song joined to its
    lyrical_charger ingestion. The `source` field lives in ingestion.detail,
    ip_address on the ingestion, the calibration on songs."""
    detail = row["detail"]
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except Exception:
            detail = {}
    detail = detail or {}
    return {
        "id": row["id"],
        "title": row["title"],
        "artist": row["artist"],
        "rubric_color": row["rubric_color"],
        "charge_value": row["charge_value"],
        "contaminated": bool(row["contaminated"]),
        "contamination_note": row["contamination_note"],
        "dogma_referenced": bool(row["dogma_referenced"]),
        "dogma_note": row["dogma_note"],
        "charge_summary": row["charge_summary"],
        "confidence": row["confidence"],
        "source": detail.get("source") or "paste_lyrics",
        "ip_address": row["ip_address"],
        "submitted_at": row["submitted_at"],
    }


def _fetch_submission(db: Session, song_id: int):
    """Fetch one submission (song + its lyrical_charger ingestion) by song id."""
    return db.execute(
        text(_SUB_SELECT + " WHERE s.id = :s ORDER BY s.id, i.id DESC"),
        {"s": song_id},
    ).mappings().first()


@router.get("", response_model=list[SubmittedSongOut], dependencies=[Depends(verify_admin_key)])
def list_submissions(
    color: str | None = Query(None),
    source: str | None = Query(None),
    contaminated: bool | None = Query(None),
    since: date | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List Lyrical Charger submissions (songs with a lyrical_charger ingestion)
    with optional filters."""
    sql = _SUB_SELECT
    params: dict = {"lim": limit, "off": offset}
    if color:
        sql += " AND s.rubric_color = :color"
        params["color"] = color
    if source:
        sql += " AND i.detail IS NOT NULL AND (i.detail::jsonb ->> 'source') = :source"
        params["source"] = source
    if contaminated is not None:
        sql += " AND s.contaminated = :contam"
        params["contam"] = contaminated
    if since:
        sql += " AND s.created_at >= :since"
        params["since"] = datetime.combine(since, datetime.min.time())
    # DISTINCT ON requires the distinct expression to lead ORDER BY; the outer
    # newest-first ordering + paging is applied to the de-duplicated set.
    sql = (
        "SELECT * FROM (" + sql + " ORDER BY s.id, i.id DESC) sub"
        " ORDER BY sub.submitted_at DESC, sub.id DESC OFFSET :off LIMIT :lim"
    )
    rows = db.execute(text(sql), params).mappings().all()
    return [_sub_out(r) for r in rows]


@router.get("/stats", response_model=SubmissionStatsOut, dependencies=[Depends(verify_admin_key)])
def submission_stats(db: Session = Depends(get_db)):
    """Aggregate stats over Lyrical Charger submissions. Each distinct song that
    carries a lyrical_charger ingestion counts once."""
    # Base set: one row per song that has a lyrical_charger ingestion, with the
    # source pulled off the (latest) such ingestion's detail.
    base = """
        SELECT DISTINCT ON (s.id) s.id AS id, s.rubric_color AS rubric_color,
               s.contaminated AS contaminated, s.confidence AS confidence,
               s.created_at AS created_at,
               (i.detail::jsonb ->> 'source') AS source
        FROM songs s
        JOIN song_ingestions i ON i.song_id = s.id AND i.method = 'lyrical_charger'
        ORDER BY s.id, i.id DESC
    """

    total = db.execute(
        text("SELECT count(*) FROM (" + base + ") b")
    ).scalar() or 0

    today_start = datetime.combine(date.today(), datetime.min.time())
    today_count = db.execute(
        text("SELECT count(*) FROM (" + base + ") b WHERE b.created_at >= :t"),
        {"t": today_start},
    ).scalar() or 0

    color_rows = db.execute(
        text("SELECT b.rubric_color, count(*) FROM (" + base + ") b GROUP BY b.rubric_color")
    ).all()
    by_color = {row[0]: row[1] for row in color_rows}

    source_rows = db.execute(
        text(
            "SELECT coalesce(b.source, 'paste_lyrics') AS source, count(*) "
            "FROM (" + base + ") b GROUP BY coalesce(b.source, 'paste_lyrics')"
        )
    ).all()
    by_source = {row[0]: row[1] for row in source_rows}

    contam = db.execute(
        text("SELECT count(*) FROM (" + base + ") b WHERE b.contaminated = true")
    ).scalar() or 0

    avg_conf = db.execute(
        text("SELECT avg(b.confidence) FROM (" + base + ") b")
    ).scalar()

    return SubmissionStatsOut(
        total=total,
        today=today_count,
        by_color=by_color,
        by_source=by_source,
        contaminated_count=contam,
        avg_confidence=round(avg_conf, 3) if avg_conf is not None else None,
    )


@router.delete("/{submission_id}", dependencies=[Depends(verify_admin_key)])
def delete_submission(submission_id: int, db: Session = Depends(get_db)):
    """Remove a submission. Deletes the lyrical_charger ingestion; if that leaves
    the songs row a pure submission artifact (no chart appearance, no other
    ingestion, not referenced by any reading / draft / release), the song and its
    directly-owned reference rows are removed too -- mirroring the legacy 'the
    submitted_songs row disappears'. A song that has since charted or been
    referenced is kept (only the lyrical_charger ingestion drops)."""
    row = _fetch_submission(db, submission_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Submission ID {submission_id} not found")
    sid = row["id"]
    title, artist = row["title"], row["artist"]

    db.execute(
        text("DELETE FROM song_ingestions WHERE song_id = :s AND method = 'lyrical_charger'"),
        {"s": sid},
    )

    orphan = (
        not db.execute(text("SELECT 1 FROM song_ingestions WHERE song_id = :s LIMIT 1"), {"s": sid}).scalar()
        and not db.execute(text("SELECT 1 FROM chart_appearances WHERE song_id = :s LIMIT 1"), {"s": sid}).scalar()
        and not db.execute(text("SELECT 1 FROM reading_songs WHERE song_id = :s LIMIT 1"), {"s": sid}).scalar()
        and not db.execute(text("SELECT 1 FROM agent_draft_songs WHERE song_id = :s LIMIT 1"), {"s": sid}).scalar()
        and not db.execute(text("SELECT 1 FROM release_songs WHERE unified_song_id = :s LIMIT 1"), {"s": sid}).scalar()
    )
    if orphan:
        for t in ("song_artists", "song_slugs", "user_calibrations", "calibration_runs",
                  "misread_submissions"):
            db.execute(text(f"DELETE FROM {t} WHERE unified_song_id = :s"), {"s": sid})
        db.execute(text("DELETE FROM song_id_map WHERE new_song_id = :s"), {"s": sid})
        db.execute(text("DELETE FROM songs WHERE id = :s"), {"s": sid})
    db.commit()
    return {"deleted": submission_id, "title": title, "artist": artist, "song_removed": orphan}


@router.post("/{submission_id}/promote", dependencies=[Depends(verify_admin_key)])
def promote_submission(submission_id: int, db: Session = Depends(get_db)):
    """Promote a submission into the authoritative Library.

    In the unified model the song already IS in the Library, so promotion no
    longer copies a row into a second table. It (a) elevates the canonical
    calibration method to authoritative 'editorial' -- so a later crowd read
    can't override it -- and (b) records an editorial ingestion breadcrumb
    (guarded against a duplicate). Does NOT delete the lyrical_charger
    ingestion."""
    row = _fetch_submission(db, submission_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Submission ID {submission_id} not found")

    if not row["title"] or not row["artist"]:
        raise HTTPException(status_code=422, detail="Cannot promote: title and artist are required")

    sid = row["id"]

    db.execute(
        text("UPDATE songs SET canonical_calibration_method = 'editorial' WHERE id = :i"),
        {"i": sid},
    )
    # Editorial ingestion breadcrumb (one per song).
    if not db.execute(
        text("SELECT 1 FROM song_ingestions WHERE song_id = :s AND method = 'editorial' LIMIT 1"),
        {"s": sid},
    ).scalar():
        db.execute(
            text("INSERT INTO song_ingestions (song_id, method, detail) VALUES (:s, 'editorial', :d)"),
            {"s": sid, "d": json.dumps({"promoted_from": "lyrical_charger"})},
        )
    db.commit()

    return {
        "promoted": True,
        "submission_id": submission_id,
        "song_id": sid,
        "title": row["title"],
        "artist": row["artist"],
    }
