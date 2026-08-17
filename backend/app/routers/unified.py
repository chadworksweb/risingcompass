"""Unified Charge Chart: public read + the editorial/publish lane.

The synthesis of the four daily charts. Composition and persistence live in
services/unified_chart.py and services/unified_store.py; this is the HTTP skin.

TWO THINGS THAT MAKE THIS ROUTER DIFFERENT FROM chart_snapshots.py

1. There is no `key`. The unified chart is a single derived chart, not a registry
   of feeds, so the routes carry no chart selector.
2. PUBLISHING IS THE EDITORIAL WRITE. Every other chart publishes when its draft
   is approved. This one composes automatically as its constituents land and then
   waits: `POST /api/admin/unified/editorial` attaches the prose and flips
   `published` in the same call. That is what keeps the number and the prose in
   lockstep, because the unified reading recomposes through the day and an
   editorial written against a three-of-four composition describes a figure that
   no longer exists once the fourth lands. See the scope, 6.3 and 8.6.
"""

import json
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import verify_admin_or_lyrics_key
from app.constants import UNIFIED_CONSTITUENT_SLUGS, chart_source_label
from app.database import get_db
from app.models import (
    ChartSnapshot,
    DailyReading,
    Song,
    UnifiedReading,
    UnifiedReadingSong,
)
from app.schemas import (
    UnifiedEditorialIn,
    UnifiedReadingOut,
    UnifiedSongOut,
    UnifiedSourceOut,
)
from app.routers.admin import verify_admin_key
from app.services import unified_store
from app.services.artist_utils import (
    generate_song_slug,
    normalize_artist_name,
    resolve_artist_slugs,
)

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/api/compass/unified", tags=["unified-chart"])
admin_router = APIRouter(prefix="/api/admin/unified", tags=["unified-chart-admin"])

_DAILY_READING_SLUG = "spotify_top50_usa"


def _dominant_topic(song: Song | None) -> str | None:
    """First topic off the song's ether tags. Mirrors chart_snapshots._dominant_topic."""
    if not song or not getattr(song, "topics", None):
        return None
    try:
        topics = json.loads(song.topics)
    except (ValueError, TypeError):
        return None
    if isinstance(topics, list) and topics:
        first = topics[0]
        return first if isinstance(first, str) else None
    return None


def _constituent_degrees(db: Session, on_date: date) -> dict[str, tuple]:
    """Each constituent's OWN reading for the day, for the composition strip.

    This is the data behind the spread, which is the finding the unified page
    exists to show: the same culture reading +2 on a purchase chart and -62 on an
    algorithmic one. Read from the stored per-chart aggregates rather than
    recomputed, so the strip agrees with what each chart's own page displays.
    """
    out: dict[str, tuple] = {}

    reading = db.query(DailyReading).filter(DailyReading.date == on_date).one_or_none()
    if reading:
        out[_DAILY_READING_SLUG] = (reading.compass_degree, reading.charge_level)

    rows = (
        db.query(ChartSnapshot.chart_source,
                 ChartSnapshot.compass_degree,
                 ChartSnapshot.charge_level)
        .filter(
            ChartSnapshot.date == on_date,
            ChartSnapshot.published.is_(True),
            ChartSnapshot.compass_degree.isnot(None),
            ChartSnapshot.chart_source.in_(UNIFIED_CONSTITUENT_SLUGS),
        )
        .distinct()
        .all()
    )
    for slug, degree, level in rows:
        out[slug] = (degree, level)
    return out


def _shape(db: Session, row: UnifiedReading) -> UnifiedReadingOut:
    """Build the public payload, shaped so chart-shell.js can render it unchanged."""
    song_rows = (
        db.query(UnifiedReadingSong, Song)
        .join(Song, UnifiedReadingSong.song_id == Song.id)
        .filter(UnifiedReadingSong.reading_id == row.id)
        .order_by(UnifiedReadingSong.position.asc())
        .all()
    )

    slug_map = resolve_artist_slugs([s.artist for _, s in song_rows], db)
    songs = [
        UnifiedSongOut(
            id=urs.id,
            title=song.title,
            artist=song.artist,
            position=urs.position,
            rubric_color=song.rubric_color,
            charge_value=song.charge_value,
            contaminated=bool(song.contaminated),
            contamination_note=song.contamination_note,
            charge_summary=song.charge_summary,
            # The union has no single chart_source by definition; `sources`
            # carries the real answer and the strip renders it.
            chart_source=None,
            instrumental=bool(song.instrumental),
            lyrics_unavailable=bool(song.lyrics_unavailable),
            preorder=bool(song.preorder),
            song_slug=generate_song_slug(song.title, song.artist),
            artist_slug=slug_map.get(normalize_artist_name(song.artist or "").lower()),
            deadpan_line=song.deadpan_line,
            dominant_topic=_dominant_topic(song),
            unified_weight=urs.unified_weight,
            chart_count=urs.chart_count,
            sources=json.loads(urs.sources or "{}"),
        )
        for urs, song in song_rows
    ]

    degrees = _constituent_degrees(db, row.date)
    try:
        included = json.loads(row.sources_included or "[]")
    except (ValueError, TypeError):
        included = []
    sources = []
    for entry in included:
        slug = entry.get("slug", "")
        deg, level = degrees.get(slug, (None, None))
        sources.append(UnifiedSourceOut(
            slug=slug,
            label=chart_source_label(slug),
            weight=entry.get("weight", 1.0),
            slots=entry.get("slots", 0),
            eligible=entry.get("eligible", 0),
            coverage=entry.get("coverage", 1.0),
            compass_degree=deg,
            charge_level=level,
        ))

    try:
        excluded = json.loads(row.sources_excluded or "[]")
    except (ValueError, TypeError):
        excluded = []

    return UnifiedReadingOut(
        date=row.date,
        compass_degree=row.compass_degree,
        charge_level=row.charge_level,
        contamination_count=row.contamination_count,
        song_count=row.song_count,
        source_count=row.source_count,
        editorial=row.editorial,
        editorial_stale=bool(row.editorial_stale),
        published=bool(row.published),
        weights_version=row.weights_version or "",
        songs=songs,
        sources_included=sources,
        sources_excluded=excluded,
    )


# --- public reads ---------------------------------------------------------- #
# Literal paths are declared BEFORE the parameterised one so /daily-chart is not
# swallowed as a date.


@public_router.get("/daily-chart")
def get_unified_daily_chart(days: int = 365, db: Session = Depends(get_db)):
    """Trailing N days of published unified degrees.

    Same shape as /api/compass/daily-chart and /api/compass/chart/{key}/daily-chart
    so the shared DailyChargePanel capsule renders it with no changes.
    """
    cutoff = date.today() - timedelta(days=days)
    rows = (
        db.query(UnifiedReading.date,
                 UnifiedReading.compass_degree,
                 UnifiedReading.charge_level)
        .filter(UnifiedReading.published.is_(True), UnifiedReading.date >= cutoff)
        .order_by(UnifiedReading.date.asc())
        .all()
    )
    return [{"date": d.isoformat(), "compass_degree": deg, "charge_level": lvl}
            for d, deg, lvl in rows]


@public_router.get("/current", response_model=UnifiedReadingOut)
def get_current_unified(db: Session = Depends(get_db)):
    """The most recent PUBLISHED unified reading.

    404 while today's is composed but has no editorial yet, which is the normal
    state between the last constituent approval and the editorial write. The
    frontend hides the panel on a 404, exactly as the other chart pages do.
    """
    row = (
        db.query(UnifiedReading)
        .filter(UnifiedReading.published.is_(True))
        .order_by(UnifiedReading.date.desc())
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="No published unified reading")
    return _shape(db, row)


@public_router.get("/reading/{reading_date}", response_model=UnifiedReadingOut)
def get_unified_reading(reading_date: date, db: Session = Depends(get_db)):
    """One published unified day."""
    row = (
        db.query(UnifiedReading)
        .filter(UnifiedReading.date == reading_date,
                UnifiedReading.published.is_(True))
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="No published unified reading for this date")
    return _shape(db, row)


# --- admin: recompose + the editorial/publish gate ------------------------- #


@admin_router.post("/recompose", dependencies=[Depends(verify_admin_key)])
def recompose_unified(reading_date: date | None = None, db: Session = Depends(get_db)):
    """Force a recompose of one day.

    The approval hook already does this automatically. This is the manual lever
    for a day whose constituents were corrected after the fact, or for a date
    that predates the hook.
    """
    target = reading_date or date.today()
    row = unified_store.recompose(db, target)
    if row is None:
        return {
            "composed": False,
            "date": target.isoformat(),
            "reason": f"fewer than {unified_store.MIN_SOURCES} constituents available",
        }
    return {
        "composed": True,
        "date": target.isoformat(),
        "compass_degree": row.compass_degree,
        "charge_level": row.charge_level,
        "song_count": row.song_count,
        "source_count": row.source_count,
        "published": row.published,
        "editorial_stale": row.editorial_stale,
    }


@admin_router.post("/editorial", dependencies=[Depends(verify_admin_or_lyrics_key)])
def supply_unified_editorial(
    data: UnifiedEditorialIn,
    reading_date: date | None = None,
    db: Session = Depends(get_db),
):
    """Attach the editorial and PUBLISH. Terminal-supplied, like every RC editorial.

    The server has no editorial-generation path at all (`_generate_editorial` has
    been a None-returning stub since the Decoupling), so Claude Code writes this
    in-session and ships it here on the lyrics-supply key lane, the same lane
    scripts/calibrate_song.py and scripts/set_editorial.py use.

    THE SAME GUARD AS EVERY OTHER EDITORIAL, pointed at the union's titles. It
    bites harder here: a single chart bans its 20 titles from the prose, while the
    union bans roughly 65, and `titles_multiword_only=False` means one-word titles
    count too. That is deliberate and matches the album lane, where a one-word
    track name is the leak at scale. Write around it; do not loosen it.
    """
    target = reading_date or date.today()
    row = (
        db.query(UnifiedReading)
        .filter(UnifiedReading.date == target)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No composed unified reading for {target}. "
                   "Approve its constituents or POST /api/admin/unified/recompose first.",
        )

    # Publishing against a partial composition freezes prose onto a figure that a
    # later approval will move. Refuse unless the caller says they mean it.
    expected = len(UNIFIED_CONSTITUENT_SLUGS)
    if row.source_count < expected and not data.force:
        try:
            excluded = json.loads(row.sources_excluded or "[]")
        except (ValueError, TypeError):
            excluded = []
        raise HTTPException(
            status_code=409,
            detail=(
                f"Only {row.source_count} of {expected} constituents composed for "
                f"{target}: {excluded}. Approve the rest, or pass force=true to "
                "publish a partial day deliberately."
            ),
        )

    editorial = (data.editorial or "").strip()
    if not editorial:
        raise HTTPException(status_code=400, detail="editorial is empty")

    titles = [
        t for (t,) in db.query(Song.title)
        .join(UnifiedReadingSong, UnifiedReadingSong.song_id == Song.id)
        .filter(UnifiedReadingSong.reading_id == row.id)
        .all()
    ]
    from app.services.agents.summary_guard import (
        SUMMARY_RULES_NUDGE,
        summary_violations,
    )
    violations = summary_violations(
        editorial,
        titles=titles,
        check_absence=False,
        titles_multiword_only=False,
    )
    if violations:
        raise HTTPException(
            status_code=400,
            detail="editorial tripped the summary guard: " + "; ".join(violations)
            + ". " + SUMMARY_RULES_NUDGE,
        )

    published = unified_store.publish(db, target, editorial)
    return {
        "date": target.isoformat(),
        "published": published.published,
        "editorial_stale": published.editorial_stale,
        "compass_degree": published.compass_degree,
        "charge_level": published.charge_level,
        "song_count": published.song_count,
        "source_count": published.source_count,
    }
