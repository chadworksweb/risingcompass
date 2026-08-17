"""Lobby comment ops + moderation logic.

Two policy enforcers live here, in service code rather than in the router,
so the API surface stays a thin shell:

  enforce_rate_limit -- rejects writes that exceed per-account thresholds.
                        10/hr 30/day for accounts < 7 days old, 30/hr
                        100/day after. Suspended users always 403.

  apply_auto_hide    -- after a new report lands, count distinct pending
                        reporters in the last 24h. If >= 3, set hidden_at
                        on the comment and log an auto_hide event.

The rate-limit thresholds live here as constants so the admin tooling can
read them for "limits" display without divergence.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Comment, CommentReport, ModerationEvent, User
from app.services import alerts as alerts_svc
from app.services import notifications as notifications_svc

logger = logging.getLogger(__name__)


# Per build plan section 1.7. Tunable from here.
RATE_LIMITS = {
    "new_account_hour": 10,
    "new_account_day": 30,
    "established_hour": 30,
    "established_day": 100,
    "new_account_age_days": 7,
}

AUTO_HIDE_DISTINCT_REPORTERS = 3
AUTO_HIDE_WINDOW_HOURS = 24
MAX_CONTENT_LENGTH = 5000
MIN_CONTENT_LENGTH = 1

VALID_TARGET_TYPES = {"song", "artist", "release"}
VALID_TARGET_SOURCES = {"songs", "compass", "library", "submitted"}
VALID_REPORT_REASONS = {
    "spam", "harassment", "off_topic", "misinformation", "other",
}


def _is_new_account(user: User, now: datetime) -> bool:
    age = now - user.created_at
    return age < timedelta(days=RATE_LIMITS["new_account_age_days"])


def enforce_rate_limit(db: Session, user: User) -> None:
    """Raise 429 if this user has hit either the hour or day cap. 403 if
    they're suspended or banned."""
    if user.status == "banned":
        raise HTTPException(status_code=403, detail="Account banned")
    now = datetime.now(timezone.utc)
    if user.suspended_until and user.suspended_until > now:
        until = user.suspended_until.isoformat() + "Z"
        raise HTTPException(
            status_code=403,
            detail=f"Posting suspended until {until}",
        )

    new_account = _is_new_account(user, now)
    hour_cap = RATE_LIMITS["new_account_hour"] if new_account else RATE_LIMITS["established_hour"]
    day_cap = RATE_LIMITS["new_account_day"] if new_account else RATE_LIMITS["established_day"]

    hour_count = (
        db.query(func.count(Comment.id))
        .filter(Comment.author_id == user.id)
        .filter(Comment.created_at >= now - timedelta(hours=1))
        .scalar()
    )
    if hour_count >= hour_cap:
        raise HTTPException(
            status_code=429,
            detail=f"Hour cap reached ({hour_cap} comments / hour). Try again later.",
        )

    day_count = (
        db.query(func.count(Comment.id))
        .filter(Comment.author_id == user.id)
        .filter(Comment.created_at >= now - timedelta(days=1))
        .scalar()
    )
    if day_count >= day_cap:
        raise HTTPException(
            status_code=429,
            detail=f"Day cap reached ({day_cap} comments / day). Try again tomorrow.",
        )


def normalize_content(raw: str) -> str:
    """Trim and reject empty / oversized content. Returns the cleaned string."""
    if raw is None:
        raise HTTPException(status_code=422, detail="Content required")
    cleaned = raw.strip()
    if len(cleaned) < MIN_CONTENT_LENGTH:
        raise HTTPException(status_code=422, detail="Comment cannot be empty")
    if len(cleaned) > MAX_CONTENT_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Comment exceeds {MAX_CONTENT_LENGTH} characters",
        )
    return cleaned


def validate_target(target_type: str, target_source: Optional[str]) -> None:
    if target_type not in VALID_TARGET_TYPES:
        raise HTTPException(status_code=422, detail=f"Unknown target_type: {target_type}")
    if target_type == "song":
        if target_source not in VALID_TARGET_SOURCES:
            raise HTTPException(
                status_code=422,
                detail="Songs require target_source = songs | compass | library | submitted",
            )
    elif target_source is not None:
        raise HTTPException(
            status_code=422,
            detail=f"target_source only applies to songs, not {target_type}",
        )


def create_top_level(
    db: Session,
    user: User,
    target_type: str,
    target_source: Optional[str],
    target_id: int,
    raw_content: str,
) -> Comment:
    validate_target(target_type, target_source)
    enforce_rate_limit(db, user)
    content = normalize_content(raw_content)

    comment = Comment(
        author_id=user.id,
        target_type=target_type,
        target_source=target_source,
        target_id=target_id,
        parent_id=None,
        thread_root_id=0,  # patched below
        content=content,
        content_length=len(content),
    )
    db.add(comment)
    db.flush()
    comment.thread_root_id = comment.id
    db.commit()
    db.refresh(comment)
    _fire_comment_created_alert(comment, user)
    # Top-level comments can still @mention users (no parent -> no reply notif).
    notifications_svc.create_for_comment(db, comment, user, parent=None)
    return comment


def _fire_comment_created_alert(comment: Comment, author: User) -> None:
    """Fan out to the alerts service. Handle is required (writes are
    gated on it) but defend against bad state just in case."""
    try:
        alerts_svc.emit_comment_created(
            handle=author.handle or author.anon_id,
            target_type=comment.target_type,
            target_source=comment.target_source,
            target_id=comment.target_id,
            content=comment.content,
            comment_id=comment.id,
        )
    except Exception:
        logger.exception("Failed to emit comment_created alert (comment=%s)", comment.id)


def create_reply(db: Session, user: User, parent_id: int, raw_content: str) -> Comment:
    parent = db.query(Comment).filter(Comment.id == parent_id).first()
    if parent is None or parent.deleted_at is not None:
        # Admin-deleted: gone for all intents and purposes.
        raise HTTPException(status_code=404, detail="Parent comment not found")
    if parent.withdrawn_at is not None:
        raise HTTPException(status_code=409, detail="The parent comment was withdrawn")
    if parent.hidden_at is not None:
        raise HTTPException(status_code=409, detail="Cannot reply to a hidden comment")
    if parent.parent_id is not None:
        # 2-level cap -- replies to replies are not allowed
        raise HTTPException(
            status_code=409,
            detail="Replies are only allowed on top-level comments",
        )

    enforce_rate_limit(db, user)
    content = normalize_content(raw_content)

    reply = Comment(
        author_id=user.id,
        target_type=parent.target_type,
        target_source=parent.target_source,
        target_id=parent.target_id,
        parent_id=parent.id,
        thread_root_id=parent.id,
        content=content,
        content_length=len(content),
    )
    db.add(reply)
    db.commit()
    db.refresh(reply)
    _fire_comment_created_alert(reply, user)
    notifications_svc.create_for_comment(db, reply, user, parent=parent)
    return reply


def withdraw_own(db: Session, user: User, comment_id: int) -> Comment:
    """User-initiated take-back. Sets withdrawn_at. Attribution stays
    public and the original content remains revealable via press-and-hold
    -- the row is still part of the historical record."""
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if comment is None or comment.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.author_id != user.id:
        raise HTTPException(status_code=403, detail="Not your comment")
    if comment.withdrawn_at is not None:
        return comment  # idempotent
    comment.withdrawn_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(comment)
    return comment


def file_report(
    db: Session,
    reporter: User,
    comment_id: int,
    reason: str,
    notes: Optional[str],
) -> tuple[CommentReport, bool]:
    """Record a report and return (report, auto_hidden_now). The second flag
    is True iff this report tipped the comment past the auto-hide threshold."""
    if reason not in VALID_REPORT_REASONS:
        raise HTTPException(status_code=422, detail=f"Unknown reason: {reason}")
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if comment is None or comment.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.withdrawn_at is not None:
        raise HTTPException(status_code=409, detail="The comment was withdrawn; nothing to report")
    if comment.author_id == reporter.id:
        raise HTTPException(status_code=409, detail="Cannot report your own comment")

    # Dedupe -- UNIQUE constraint backs this up but we want a clean 200
    # response instead of a 500 on the integrity error.
    existing = (
        db.query(CommentReport)
        .filter(CommentReport.comment_id == comment_id)
        .filter(CommentReport.reporter_id == reporter.id)
        .first()
    )
    if existing is not None:
        return existing, False

    report = CommentReport(
        comment_id=comment_id,
        reporter_id=reporter.id,
        reason=reason,
        notes=(notes or None),
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    auto_hidden = apply_auto_hide(db, comment)
    return report, auto_hidden


def apply_auto_hide(db: Session, comment: Comment) -> bool:
    """If the comment now has 3+ distinct pending reporters within the last
    24h, hide it and log an auto_hide event. Returns True if a hide was
    just applied (i.e., the comment was not already hidden)."""
    if comment.hidden_at is not None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=AUTO_HIDE_WINDOW_HOURS)
    distinct = (
        db.query(func.count(func.distinct(CommentReport.reporter_id)))
        .filter(CommentReport.comment_id == comment.id)
        .filter(CommentReport.status == "pending")
        .filter(CommentReport.created_at >= cutoff)
        .scalar()
    )
    if distinct < AUTO_HIDE_DISTINCT_REPORTERS:
        return False
    comment.hidden_at = datetime.now(timezone.utc)
    comment.hidden_reason = (
        f"Auto-hidden after {distinct} reports in the last "
        f"{AUTO_HIDE_WINDOW_HOURS}h. An admin will review."
    )
    db.add(ModerationEvent(
        action="auto_hide",
        target_user_id=comment.author_id,
        target_comment_id=comment.id,
        reason=comment.hidden_reason,
    ))
    db.commit()
    db.refresh(comment)
    logger.info("Auto-hid comment %s after %s distinct reports", comment.id, distinct)
    return True
