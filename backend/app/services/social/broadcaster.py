"""Build 6 broadcaster -- select -> render -> push -> ledger, once per run.

Each run broadcasts up to N (default 3) trending calibrated per-song verdicts
plus the day's daily-aggregate reading, in RC's "Plain instrument" voice, to RC's
own Buffer queue (fan-out to every connected platform). Every (item, platform) is
recorded in `social_posts` keyed by a unique dedup_key, so nothing is broadcast
twice and the next run cheaply excludes what already went out.

Selection sources:
- trending = the latest Shazam + YouTube snapshot rows, resolved to calibrated
  songs, ranked by |charge| (the strongest verdict travels), top N, excluding
  any song already broadcast.
- reading = the most recent DailyReading, unless it has already been broadcast.

Ships DARK: when Buffer is not configured the pipeline still selects, renders,
stores the card, and writes the ledger rows -- as status='dark', not pushed --
so the whole machine is verifiable before any account or credential exists.
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
from app.services.artist_utils import generate_song_slug
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

# Trending feeders (Build 1): the social-discovery gutter charts. (source tag,
# chart_snapshots.chart_source slug).
TRENDING_SOURCES = [
    ("shazam", "shazam_top200_usa"),
    ("youtube", "youtube_trending_usa"),
]

# The six locked platforms (Build 6). Used for the dark/ledger fan-out when no
# Buffer profile map is configured yet; once configured, profile_map() drives it.
DEFAULT_PLATFORMS = ["x", "bluesky", "threads", "instagram", "tiktok", "facebook"]

# A post is "already broadcast" only once it is actually out (or queued). 'dark'
# rows are NOT live -- they can be upgraded to a real post on a later configured
# run, so selection still picks them up.
LIVE_STATUSES = ("queued", "posted")


def _parse_topics(raw):
    import json
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [t for t in parsed if isinstance(t, str)][:3] if isinstance(parsed, list) else []


def _reading_score(reading: DailyReading) -> int:
    return round((90 - reading.compass_degree) * 100 / 90)


def _platforms() -> list[str]:
    return list(buffer_client.profile_map().keys()) or DEFAULT_PLATFORMS


def _tagged_link(path: str, campaign_scope: str) -> str:
    base = settings.social_link_base.rstrip("/")
    return (f"{base}{path}?utm_source=social&utm_medium=buffer"
            f"&utm_campaign=rc_broadcast_{campaign_scope}")


# --- selection ------------------------------------------------------------

def _broadcast_song_ids(db) -> set[int]:
    rows = db.query(SocialPost.song_id).filter(
        SocialPost.scope == "song",
        SocialPost.song_id.isnot(None),
        SocialPost.status.in_(LIVE_STATUSES),
    ).all()
    return {sid for (sid,) in rows if sid}


def select_trending(db, limit: int) -> list[dict]:
    """Top trending calibrated songs not yet broadcast, ranked by |charge|."""
    already = _broadcast_song_ids(db)
    candidates: dict[int, dict] = {}
    for source, slug in TRENDING_SOURCES:
        latest = db.query(func.max(ChartSnapshot.date)).filter(
            ChartSnapshot.chart_source == slug
        ).scalar()
        if not latest:
            continue
        rows = db.query(ChartSnapshot).filter(
            ChartSnapshot.chart_source == slug,
            ChartSnapshot.date == latest,
        ).order_by(ChartSnapshot.position).all()
        for r in rows:
            song = find_song_by_title_artist(db, r.title, r.artist)
            if not song or not song.rubric_color or song.charge_value is None:
                continue  # uncalibrated -> not a verdict we can post (no Opus spend)
            if song.instrumental or song.preorder:
                continue
            if song.id in already or song.id in candidates:
                continue
            candidates[song.id] = {"song": song, "source": source, "position": r.position}
    ranked = sorted(candidates.values(), key=lambda c: abs(c["song"].charge_value), reverse=True)
    return ranked[:limit]


def select_reading(db) -> DailyReading | None:
    """The latest daily reading, unless it has already been broadcast."""
    reading = (
        db.query(DailyReading)
        .options(joinedload(DailyReading.songs).joinedload(ReadingSong.song))
        .order_by(DailyReading.date.desc())
        .first()
    )
    if not reading:
        return None
    posted = db.query(SocialPost.id).filter(
        SocialPost.scope == "reading",
        SocialPost.reading_date == reading.date,
        SocialPost.status.in_(LIVE_STATUSES),
    ).first()
    return None if posted else reading


# --- payloads (card data + post text) -------------------------------------

def _song_card_data(song) -> dict:
    return {
        "title": song.title,
        "artist": song.artist,
        "charge": song.charge_value,
        "tier": song.rubric_color,
        "tier_label": TIER_LABELS.get(song.rubric_color, ""),
        "deadpan_line": song.deadpan_line or "",
        "charge_summary": song.charge_summary or "",
        "topics": _parse_topics(song.topics),
    }


def _reading_card_data(reading: DailyReading) -> dict:
    songs = []
    for rs in sorted(reading.songs, key=lambda s: s.position):
        s = rs.song
        songs.append({
            "position": rs.position,
            "title": rs.title,
            "artist": rs.artist,
            "rubric_color": s.rubric_color if s else None,
            "charge_value": s.charge_value if s else None,
            "instrumental": bool(s.instrumental) if s else False,
            "preorder": bool(s.preorder) if s else False,
        })
    return {
        "date": reading.date.isoformat(),
        "degree": reading.compass_degree,
        "charge": reading.charge_level,
        "contaminationCount": reading.contamination_count,
        "editorial": reading.editorial_summary or "",
        "songs": songs,
    }


def _song_post_text(song) -> str:
    cs = ("+" if song.charge_value >= 0 else "") + str(song.charge_value)
    label = TIER_LABELS.get(song.rubric_color, "")
    link = _tagged_link(f"/songs/{generate_song_slug(song.title, song.artist)}", "song")
    return (f'Rising Compass measured "{song.title}" by {song.artist}.\n'
            f'Charge: {cs} ({label}).\n\n{link}')


def _reading_post_text(reading: DailyReading) -> str:
    score = _reading_score(reading)
    ss = ("+" if score > 0 else "") + str(score)
    label = TIER_LABELS.get(reading.charge_level, "")
    n = len(reading.songs)
    m = reading.contamination_count
    d = reading.date
    datestr = f"{month_name[d.month]} {d.day}, {d.year}"
    link = _tagged_link("/charts/daily-listens/", "reading")
    return (f"The Rising Compass daily reading for {datestr}: {ss} ({label}).\n"
            f"{n} songs measured, {m} contaminated.\n\n{link}")


# --- card store + ledger --------------------------------------------------

def _store_card(db, scope: str, ref: str, png: bytes) -> str:
    token = secrets.token_urlsafe(16)
    db.add(SocialCard(token=token, scope=scope, ref=str(ref), png=png))
    db.flush()
    return token


def _card_url(token: str) -> str:
    base = settings.social_link_base.rstrip("/")
    return f"{base}/api/social/card/{token}.png"


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

async def _broadcast_item(db, *, kind: str, card_type: str, card_scope_ref: str,
                          card_data: dict, post_text: str, song_id, reading_date,
                          charge_value, tier, trending_source, configured: bool,
                          platforms: list[str]) -> dict:
    png = await render_card(card_type, card_data)
    token = _store_card(db, kind, card_scope_ref, png)
    image_url = _card_url(token)

    ext: dict[str, str] = {}
    errs: dict[str, str] = {}
    if configured:
        pmap = buffer_client.profile_map()
        profile_ids = [pmap[p] for p in platforms if p in pmap]
        resp = await buffer_client.create_update(post_text, image_url, profile_ids)
        ext = buffer_client.map_updates_to_platforms(resp, platforms)
        errs = buffer_client.map_errors_to_platforms(resp)

    now = datetime.utcnow()
    for p in platforms:
        if kind == "song":
            dedup_key = f"song:{song_id}:{p}"
        else:
            dedup_key = f"reading:{reading_date.isoformat()}:{p}"
        # Per-platform outcome: dark when unconfigured; posted when Buffer
        # returned a post id; error otherwise (so a failed channel stays
        # non-live and is re-selected on the next run rather than looking sent).
        if not configured:
            p_status, p_ext, p_err, p_posted = "dark", None, None, None
        elif p in ext:
            p_status, p_ext, p_err, p_posted = "posted", ext[p], None, now
        else:
            p_status, p_ext, p_err, p_posted = "error", None, errs.get(p, "buffer push failed"), None
        _upsert_post(
            db,
            scope=kind,
            song_id=song_id,
            reading_date=reading_date,
            platform=p,
            dedup_key=dedup_key,
            post_text=post_text,
            card_token=token,
            charge_value=charge_value,
            tier=tier,
            trending_source=trending_source,
            post_external_id=p_ext,
            status=p_status,
            error=p_err,
            posted_at=p_posted,
        )
    db.commit()
    if not configured:
        item_status = "dark"
    elif ext and not errs:
        item_status = "posted"
    elif ext:
        item_status = "partial"
    else:
        item_status = "error"
    return {"kind": kind, "ref": card_scope_ref, "status": item_status,
            "platforms": platforms, "pushed": sorted(ext.keys()),
            "failed": sorted(errs.keys()), "card_token": token}


async def run_social_broadcast(trigger: str = "cron") -> dict:
    db = SessionLocal()
    configured = buffer_client.is_configured()
    platforms = _platforms()
    summary = {
        "trigger": trigger,
        "buffer_configured": configured,
        "platforms": platforms,
        "items": [],
        "pushed": 0,
        "dark": 0,
        "errors": 0,
    }
    try:
        work: list[tuple[str, object]] = []
        for cand in select_trending(db, settings.social_trending_count):
            work.append(("song", cand))
        reading = select_reading(db)
        if reading is not None:
            work.append(("reading", reading))

        if not work:
            summary["note"] = "nothing new to broadcast"
            return summary

        for kind, obj in work:
            try:
                if kind == "song":
                    song = obj["song"]
                    result = await _broadcast_item(
                        db, kind="song", card_type="song",
                        card_scope_ref=str(song.id),
                        card_data=_song_card_data(song),
                        post_text=_song_post_text(song),
                        song_id=song.id, reading_date=None,
                        charge_value=song.charge_value, tier=song.rubric_color,
                        trending_source=obj["source"], configured=configured,
                        platforms=platforms,
                    )
                    result["title"] = song.title
                    result["artist"] = song.artist
                else:
                    result = await _broadcast_item(
                        db, kind="reading", card_type="reading",
                        card_scope_ref=obj.date.isoformat(),
                        card_data=_reading_card_data(obj),
                        post_text=_reading_post_text(obj),
                        song_id=None, reading_date=obj.date,
                        charge_value=_reading_score(obj), tier=obj.charge_level,
                        trending_source=None, configured=configured,
                        platforms=platforms,
                    )
                summary["items"].append(result)
                if result["status"] == "dark":
                    summary["dark"] += 1
                elif result["status"] == "error":
                    summary["errors"] += 1
                else:  # posted / partial -> at least one channel landed
                    summary["pushed"] += 1
            except Exception as exc:
                db.rollback()
                logger.exception("social broadcast item failed (%s)", kind)
                summary["errors"] += 1
                summary["items"].append({"kind": kind, "status": "error", "error": str(exc)})
        return summary
    finally:
        db.close()
