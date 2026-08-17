"""Prompt-cache advisor.

Prompt caching is OFF by design (see analyzer/calibrator). It only saves money
once calibrator API traffic is dense enough that calls land within the cache
TTL of each other -- below that density a cold cache write costs MORE than no
cache at all. This module watches real calibrator traffic and emails RC admin
exactly once, when the density crosses a clear-win threshold, so caching can be
turned on at the right moment.

Why the calibrator only: it is the single Opus call whose system prompt
(RUBRIC_DEFINITION + CALIBRATION_FORMAT, ~11.5K tokens) clears Opus 4.6's
4096-token minimum cacheable prefix. The identity-guard / effects / societal
prompts are all under that floor and can never cache, so traffic to them is
irrelevant to this decision.

The check runs once a day, piggybacked on the daily reading cron (no separate
crontab entry). It is fully self-contained -- own DB session, swallows every
error -- so it can never affect the reading it rides on.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_

from app.database import SessionLocal
from app.models import ClaudeApiUsage, SystemFlag

logger = logging.getLogger(__name__)

# --- Tunables --------------------------------------------------------------
# The decision is driven by hit rate; the dollar figure is informational.

WINDOW_DAYS = 7              # trailing window analyzed
TTL_SECONDS = 300            # default ephemeral cache TTL (5 min)
MIN_CALLS = 150              # need real volume before trusting the pattern (~21/day)
HIT_RATE_THRESHOLD = 0.50    # well above the ~22% break-even -- only fire on a clear win
MIN_MONTHLY_SAVINGS_USD = 5  # must be materially worth the change

# Cacheable prefix size + Opus 4.6 rates (USD per 1M tokens, 5-min TTL).
# Prefix size measured from the rendered calibrator system prompt; used only
# for the informational savings projection, not the fire decision.
CACHEABLE_PREFIX_TOKENS = 11450
_RATE_INPUT = 15.0
_RATE_CACHE_READ = 1.50
_RATE_CACHE_WRITE = 18.75    # 1.25x input

# system_flags key that records we've already nudged. Presence == do not re-fire.
NOTIFIED_FLAG_KEY = "prompt_cache.advisor_notified"


def _already_notified(db) -> bool:
    return db.query(SystemFlag).filter(SystemFlag.key == NOTIFIED_FLAG_KEY).first() is not None


def _mark_notified(db) -> None:
    db.add(SystemFlag(key=NOTIFIED_FLAG_KEY, value=datetime.now(timezone.utc).isoformat()))
    db.commit()


def analyze(db) -> dict:
    """Compute the would-be cache economics over the trailing window.

    A calibrator call is a 'warm read' if the previous calibrator call (the
    cache is org-wide and prefix-keyed, so any caller keeps it warm) was within
    TTL_SECONDS. The first call after any gap is a 'cold write'.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    rows = (
        db.query(ClaudeApiUsage.ts)
        .filter(and_(
            ClaudeApiUsage.call_site == "calibrator",
            ClaudeApiUsage.ok == 1,
            ClaudeApiUsage.ts >= cutoff,
        ))
        .order_by(ClaudeApiUsage.ts.asc())
        .all()
    )
    timestamps = [r[0] for r in rows]
    total = len(timestamps)

    warm = 0
    prev = None
    for ts in timestamps:
        if prev is not None and (ts - prev).total_seconds() <= TTL_SECONDS:
            warm += 1
        prev = ts
    cold = total - warm
    hit_rate = (warm / total) if total else 0.0

    # Per-call deltas vs. no-cache baseline.
    save_per_warm = CACHEABLE_PREFIX_TOKENS * (_RATE_INPUT - _RATE_CACHE_READ) / 1_000_000
    cost_per_cold = CACHEABLE_PREFIX_TOKENS * (_RATE_CACHE_WRITE - _RATE_INPUT) / 1_000_000
    net_window = warm * save_per_warm - cold * cost_per_cold
    monthly = net_window * 30.0 / WINDOW_DAYS

    return {
        "total": total,
        "warm": warm,
        "cold": cold,
        "hit_rate": hit_rate,
        "monthly_savings_usd": monthly,
    }


def _should_fire(stats: dict) -> bool:
    return (
        stats["total"] >= MIN_CALLS
        and stats["hit_rate"] >= HIT_RATE_THRESHOLD
        and stats["monthly_savings_usd"] >= MIN_MONTHLY_SAVINGS_USD
    )


def get_notified_at(db) -> str | None:
    """ISO timestamp of when the nudge was sent, or None if not yet sent."""
    row = db.query(SystemFlag).filter(SystemFlag.key == NOTIFIED_FLAG_KEY).first()
    return row.value if row else None


def reset_notification(db) -> bool:
    """Clear the 'already nudged' flag so the advisor can fire again. Returns
    True if a flag was present and removed."""
    row = db.query(SystemFlag).filter(SystemFlag.key == NOTIFIED_FLAG_KEY).first()
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def status(db) -> dict:
    """Full advisor state for the admin UI: live projection over the window,
    the thresholds it's judged against, whether it currently meets them, and
    whether the one-time nudge has already been sent."""
    stats = analyze(db)
    notified_at = get_notified_at(db)
    return {
        "window_days": WINDOW_DAYS,
        "ttl_seconds": TTL_SECONDS,
        "cacheable_prefix_tokens": CACHEABLE_PREFIX_TOKENS,
        "thresholds": {
            "min_calls": MIN_CALLS,
            "hit_rate": HIT_RATE_THRESHOLD,
            "min_monthly_savings_usd": MIN_MONTHLY_SAVINGS_USD,
        },
        "stats": stats,
        "would_fire": _should_fire(stats),
        "notified": notified_at is not None,
        "notified_at": notified_at,
        # Caching is off in code by design; there is no runtime toggle. The UI
        # shows this so the panel reads as "advisor watching, caching off".
        "caching_enabled": False,
    }


def evaluate_and_notify() -> None:
    """Run the daily check. Emails RC admin once if caching is now worthwhile.

    Self-contained and error-swallowing -- safe to call as a side effect from
    the reading cron. Does nothing (beyond a debug log) until the threshold is
    crossed, and never fires twice.
    """
    db = SessionLocal()
    try:
        if _already_notified(db):
            return
        stats = analyze(db)
        if not _should_fire(stats):
            logger.debug(
                "cache_advisor: below threshold (calls=%s hit_rate=%.2f monthly=$%.2f)",
                stats["total"], stats["hit_rate"], stats["monthly_savings_usd"],
            )
            return

        # Import here to avoid a circular import at module load (alerts imports
        # config/db; cache_advisor is imported from the cron router).
        from app.services.alerts import emit_prompt_cache_warranted
        emit_prompt_cache_warranted(stats=stats, window_days=WINDOW_DAYS)
        _mark_notified(db)
        logger.info(
            "cache_advisor: nudge sent (calls=%s hit_rate=%.2f monthly=$%.2f)",
            stats["total"], stats["hit_rate"], stats["monthly_savings_usd"],
        )
    except Exception:
        logger.exception("cache_advisor.evaluate_and_notify failed (non-fatal)")
    finally:
        db.close()
