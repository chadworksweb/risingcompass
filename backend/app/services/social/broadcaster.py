"""Build 6 broadcaster -- the automated DAILY CHARTS broadcast.

One manually-triggered run per day publishes RC's own daily-chart readings to
its own accounts through ONE Buffer integration:

- Instagram + TikTok get a CAROUSEL of the day's published daily-chart cards,
  always led by Daily Listens, then Daily Downloads / Shazam / YouTube if each
  is published (graceful -- a missing chart is simply not a slide).
- Every other platform (X / Bluesky / Threads / Facebook) gets the single
  Daily Listens card.

Captions are objective, data-only, tuned per channel (inline UTM link where links
are clickable; "link in bio" on the carousel platforms; per-channel hashtags).

Trending per-song selection was removed: individual songs are posted by hand,
off-platform, not through this machine. Each (platform) result is recorded in
social_posts keyed by a per-date dedup_key, so a same-day re-run never double-posts.

Ships DARK: when Buffer is not configured the run still gathers + renders + stores
the cards and writes the ledger as status='dark', pushing nothing -- so the whole
pipeline is verifiable before any account or credential exists.
"""

import logging
import secrets
from calendar import month_name
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import joinedload

from app.config import settings
from app.database import SessionLocal
from app.models import ChartSnapshot, DailyReading, ReadingSong, SocialCard, SocialPost
from app.services.social import buffer_client
from app.services.social.card_render import render_card
from app.services.song_store import find_song_by_title_artist

logger = logging.getLogger(__name__)

TIER_LABELS = {
    "violet": "Ascended",
    "blue": "Elevated",
    "green": "Decent",
    "orange": "Degraded",
    "red": "Corrupted",
}

# The non-reading daily charts, in carousel order after Daily Listens.
# (label shown in the caption, chart_snapshots.chart_source slug).
DAILY_CHART_SNAPSHOTS = [
    ("Daily Downloads", "itunes_download_usa"),
    ("Shazam", "shazam_top200_usa"),
    ("YouTube", "youtube_trending_usa"),
]

# Platforms that get the multi-card carousel; everyone else gets single Daily Listens.
CAROUSEL_PLATFORMS = ("instagram", "tiktok")

# Fan-out target when no Buffer channel map is configured yet (dark ledger).
DEFAULT_PLATFORMS = ["x", "bluesky", "threads", "instagram", "tiktok", "facebook"]

# A post is "already broadcast" only once it is actually out (or queued). 'dark'
# rows are NOT live -- a later configured run can upgrade them to a real post.
LIVE_STATUSES = ("queued", "posted")


# --- small helpers --------------------------------------------------------

def _platforms() -> list[str]:
    return list(buffer_client.profile_map().keys()) or DEFAULT_PLATFORMS


def _reading_score(reading: DailyReading) -> int:
    return round((90 - reading.compass_degree) * 100 / 90)


def _score_str(score: int) -> str:
    return ("+" if score > 0 else "") + str(score)


def _long_date(d) -> str:
    return f"{month_name[d.month]} {d.day}, {d.year}"


def _tagged_link(path: str, campaign_scope: str) -> str:
    base = settings.social_link_base.rstrip("/")
    return (f"{base}{path}?utm_source=social&utm_medium=buffer"
            f"&utm_campaign=rc_broadcast_{campaign_scope}")


# --- chart gathering (card data per published daily chart) -----------------

def _reading_songs(reading: DailyReading) -> list[dict]:
    out = []
    for rs in sorted(reading.songs, key=lambda s: s.position):
        s = rs.song
        out.append({
            "position": rs.position,
            "title": rs.title,
            "artist": rs.artist,
            "rubric_color": s.rubric_color if s else None,
            "charge_value": s.charge_value if s else None,
            "instrumental": bool(s.instrumental) if s else False,
            "preorder": bool(s.preorder) if s else False,
        })
    return out


def _chart_from_reading(reading: DailyReading) -> dict:
    cd = {
        "date": reading.date.isoformat(),
        "degree": reading.compass_degree,
        "charge": reading.charge_level,
        "contaminationCount": reading.contamination_count,
        "editorial": reading.editorial_summary or "",
        "songs": _reading_songs(reading),
        "kicker": "DAILY LISTENS",
    }
    return {
        "label": "Daily Listens", "kicker": "DAILY LISTENS",
        "score": _reading_score(reading), "tier": reading.charge_level,
        "card_data": cd, "date": reading.date,
        "count": len(reading.songs), "contam": reading.contamination_count or 0,
    }


def _chart_from_snapshot(db, label: str, src: str) -> dict | None:
    """Build a reading-card chart from the latest published snapshot for `src`.
    Returns None unless the snapshot is published AND carries the aggregate
    (compass_degree) a card needs -- so an un-approved chart is simply skipped."""
    latest = db.query(func.max(ChartSnapshot.date)).filter(
        ChartSnapshot.chart_source == src
    ).scalar()
    if not latest:
        return None
    snaps = db.query(ChartSnapshot).filter(
        ChartSnapshot.chart_source == src,
        ChartSnapshot.date == latest,
    ).order_by(ChartSnapshot.position).all()
    if not snaps:
        return None
    head = snaps[0]
    if not head.published or head.compass_degree is None:
        return None  # not yet approved/painted -> not a card-ready chart today

    # ChartSnapshot stores no contamination_count -- it is counted from the
    # looked-up songs (mirrors chart_snapshots.py). Resolve every row once, count
    # contamination over all, build the card's song rows from the top 5.
    resolved = [find_song_by_title_artist(db, s.title, s.artist) for s in snaps]
    contam = sum(1 for so in resolved if so and so.contaminated)
    songs = []
    for i, s in enumerate(snaps[:5]):
        song = resolved[i]
        songs.append({
            "position": s.position,
            "title": s.title,
            "artist": s.artist,
            "rubric_color": song.rubric_color if song else None,
            "charge_value": song.charge_value if song else None,
            "instrumental": bool(song.instrumental) if song else False,
            "preorder": bool(song.preorder) if song else False,
        })
    kicker = label.upper()
    cd = {
        "date": latest.isoformat(),
        "degree": head.compass_degree,
        "charge": head.charge_level,
        "contaminationCount": contam,
        "editorial": head.editorial or "",
        "songs": songs,
        "kicker": kicker,
    }
    score = round((90 - head.compass_degree) * 100 / 90)
    return {
        "label": label, "kicker": kicker, "score": score, "tier": head.charge_level,
        "card_data": cd, "date": latest, "count": len(snaps), "contam": contam,
    }


def _gather_charts(db) -> tuple[list[dict], DailyReading | None]:
    """The day's card-ready daily charts, Daily Listens first. Requires a daily
    reading (the anchor); the other three are added only when published."""
    reading = (
        db.query(DailyReading)
        .options(joinedload(DailyReading.songs).joinedload(ReadingSong.song))
        .order_by(DailyReading.date.desc())
        .first()
    )
    if not reading:
        return [], None
    charts = [_chart_from_reading(reading)]
    for label, src in DAILY_CHART_SNAPSHOTS:
        c = _chart_from_snapshot(db, label, src)
        if c:
            charts.append(c)
    return charts, reading


# --- captions (objective, data-only, per channel) -------------------------

def _carousel_caption(charts: list[dict], date) -> str:
    lines = ["Rising Compass daily charts, " + _long_date(date)]
    for c in charts:
        lines.append(f"{c['label']}: {_score_str(c['score'])} ({TIER_LABELS.get(c['tier'], '')})")
    lines.append("Full readings: link in bio.")
    return "\n".join(lines) + "\n\n#RisingCompass #musiccharts #dailycharts"


def _daily_listens_caption(daily: dict, platform: str, date) -> str:
    line1 = (f"Rising Compass Daily Listens, {_long_date(date)}: "
             f"{_score_str(daily['score'])} ({TIER_LABELS.get(daily['tier'], '')}).")
    line2 = f"{daily['count']} measured, {daily['contam']} contaminated."
    link = _tagged_link("/charts/daily-listens/", "charts")
    tail = "\n\n#RisingCompass" if platform in ("x", "bluesky") else ""
    return f"{line1}\n{line2}\n\n{link}{tail}"


# --- card store + ledger --------------------------------------------------

def _store_card(db, scope: str, ref: str, png: bytes) -> str:
    token = secrets.token_urlsafe(16)
    db.add(SocialCard(token=token, scope=scope, ref=str(ref), png=png))
    db.flush()
    return token


def _card_url(token: str) -> str:
    base = settings.social_link_base.rstrip("/")
    return f"{base}/api/social/card/{token}.png"


def _already_live(db, dedup_key: str) -> bool:
    return db.query(SocialPost.id).filter(
        SocialPost.dedup_key == dedup_key,
        SocialPost.status.in_(LIVE_STATUSES),
    ).first() is not None


def _upsert_post(db, **vals) -> None:
    """Insert (or upgrade a non-live row to) one (item, platform) ledger row.
    ON CONFLICT keeps a live (queued/posted) row untouched."""
    stmt = pg_insert(SocialPost).values(**vals)
    update_cols = {
        "post_text": stmt.excluded.post_text,
        "card_token": stmt.excluded.card_token,
        "charge_value": stmt.excluded.charge_value,
        "tier": stmt.excluded.tier,
        "trending_source": stmt.excluded.trending_source,
        "post_external_id": stmt.excluded.post_external_id,
        "status": stmt.excluded.status,
        "error": stmt.excluded.error,
        "posted_at": stmt.excluded.posted_at,
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=["dedup_key"],
        set_=update_cols,
        where=SocialPost.status.notin_(LIVE_STATUSES),
    )
    db.execute(stmt)


# --- orchestrator ---------------------------------------------------------

async def run_social_broadcast(trigger: str = "manual") -> dict:
    db = SessionLocal()
    configured = buffer_client.is_configured()
    platforms = _platforms()
    summary = {
        "trigger": trigger,
        "buffer_configured": configured,
        "platforms": platforms,
        "items": [],
        "posted": 0,
        "dark": 0,
        "errors": 0,
    }
    try:
        charts, reading = _gather_charts(db)
        if not charts:
            summary["note"] = "no daily reading to broadcast"
            return summary
        date = reading.date
        daily = charts[0]
        summary["charts"] = [c["label"] for c in charts]

        # Render + store each chart card, then COMMIT so the public card route
        # (a separate DB session) can serve them when Buffer fetches the URLs.
        for c in charts:
            png = await render_card("reading", c["card_data"])
            c["token"] = _store_card(db, "charts", f"{c['kicker']}:{date.isoformat()}", png)
        db.commit()
        for c in charts:
            c["url"] = _card_url(c["token"])

        pmap = buffer_client.profile_map() if configured else {}
        items = []   # push items (configured only)
        plan = []    # ledger entries (always)
        for p in platforms:
            if p in CAROUSEL_PLATFORMS:
                scope, dedup = "charts", f"charts:{date.isoformat()}:{p}"
                text = _carousel_caption(charts, date)
                urls = [c["url"] for c in charts]
            else:
                scope, dedup = "reading", f"reading:{date.isoformat()}:{p}"
                text = _daily_listens_caption(daily, p, date)
                urls = [daily["url"]]
            if _already_live(db, dedup):
                continue
            plan.append({"platform": p, "scope": scope, "dedup": dedup, "text": text})
            if configured and p in pmap:
                items.append({"platform": p, "channel_id": pmap[p],
                              "text": text, "image_urls": urls})

        posted, errors = {}, {}
        if configured and items:
            res = await buffer_client.post_items(items)
            posted, errors = res["posted"], res["errors"]

        now = datetime.utcnow()
        for e in plan:
            p = e["platform"]
            if not configured:
                st, ext, err, pa = "dark", None, None, None
            elif p in posted:
                st, ext, err, pa = "posted", posted[p], None, now
            else:
                st, ext, err, pa = "error", None, errors.get(p, "buffer push failed"), None
            _upsert_post(
                db, scope=e["scope"], song_id=None, reading_date=date, platform=p,
                dedup_key=e["dedup"], post_text=e["text"], card_token=daily["token"],
                charge_value=daily["score"], tier=daily["tier"], trending_source=None,
                post_external_id=ext, status=st, error=err, posted_at=pa,
            )
            summary["items"].append({"platform": p, "scope": e["scope"], "status": st,
                                     "external_id": ext, "error": err})
            summary["posted" if st == "posted" else ("errors" if st == "error" else "dark")] += 1
        db.commit()
        if not plan:
            summary["note"] = "all platforms already broadcast for this date"
        return summary
    except Exception as exc:
        db.rollback()
        logger.exception("social broadcast failed")
        summary["errors"] += 1
        summary["items"].append({"status": "error", "error": str(exc)})
        return summary
    finally:
        db.close()
