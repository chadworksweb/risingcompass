"""Lobby comment notifications (Phase 1.5).

Creates reply + @mention notifications when a comment is posted, and reads
them back for the account-page panel. Detection runs after the comment is
already committed, in its own transaction, and never raises into the
caller -- a notification failure must not fail the comment write.

Mention syntax: @handle, where handle matches the Lobby handle rules
(3-20 chars, alphanumeric + underscore). Resolution is case-insensitive
against active users. A user who is both replied-to and mentioned in the
same comment gets a single (reply) notification, not two.
"""

import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Artist, Comment, CommentNotification, SongSlug, User

logger = logging.getLogger(__name__)

# Handle rules: 3-20 chars, alphanumeric + underscore (build plan 1.2).
_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{3,20})")
LIST_LIMIT = 30
_SNIPPET_LEN = 120


def parse_mentions(content: Optional[str]) -> list[str]:
    """Return unique @handles (case-insensitive) in posting order."""
    if not content:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for h in _MENTION_RE.findall(content):
        low = h.lower()
        if low not in seen:
            seen.add(low)
            out.append(h)
    return out


def create_for_comment(
    db: Session,
    comment: Comment,
    author: User,
    parent: Optional[Comment] = None,
) -> None:
    """Fan out reply + mention notifications for a new comment. Own commit;
    swallows errors so the comment write is never affected."""
    try:
        notified: set[int] = set()

        # Reply-to-you: the parent comment's author, unless self-reply.
        if parent is not None and parent.author_id and parent.author_id != author.id:
            db.add(CommentNotification(
                user_id=parent.author_id,
                type="reply",
                comment_id=comment.id,
                actor_id=author.id,
            ))
            notified.add(parent.author_id)

        # @mentions: resolve handles to active users; skip self and anyone
        # already notified (reply recipient wins over a duplicate mention).
        for handle in parse_mentions(comment.content):
            u = (
                db.query(User)
                .filter(func.lower(User.handle) == handle.lower())
                .filter(User.status == "active")
                .first()
            )
            if u is not None and u.id != author.id and u.id not in notified:
                db.add(CommentNotification(
                    user_id=u.id,
                    type="mention",
                    comment_id=comment.id,
                    actor_id=author.id,
                ))
                notified.add(u.id)

        if notified:
            db.commit()
    except Exception:
        logger.exception("Failed to create notifications for comment %s", comment.id)
        db.rollback()


def _resolve_link(db: Session, comment: Optional[Comment]) -> Optional[str]:
    """Deep-link to the comment in context. Songs and artists have public
    detail pages; releases do not (return None)."""
    if comment is None:
        return None
    anchor = f"#comment-{comment.id}"
    if comment.target_type == "song":
        ss = (
            db.query(SongSlug)
            .filter(SongSlug.song_source == comment.target_source)
            .filter(SongSlug.song_id == comment.target_id)
            .first()
        )
        return f"/songs/{ss.slug}{anchor}" if ss else None
    if comment.target_type == "artist":
        a = db.query(Artist).filter(Artist.id == comment.target_id).first()
        return f"/artists/{a.slug}{anchor}" if a else None
    return None


def list_for_user(db: Session, user: User, limit: int = LIST_LIMIT) -> list[dict]:
    rows = (
        db.query(CommentNotification)
        .filter(CommentNotification.user_id == user.id)
        .order_by(CommentNotification.created_at.desc())
        .limit(limit)
        .all()
    )
    out: list[dict] = []
    for n in rows:
        actor = db.query(User).get(n.actor_id) if n.actor_id else None
        c = db.query(Comment).get(n.comment_id) if n.comment_id else None
        snippet = None
        if c is not None and c.deleted_at is None and c.content:
            snippet = c.content[:_SNIPPET_LEN]
            if len(c.content) > _SNIPPET_LEN:
                snippet += "..."
        out.append({
            "id": n.id,
            "type": n.type,
            "actor_handle": actor.handle if actor is not None else None,
            "snippet": snippet,
            "link": _resolve_link(db, c),
            "created_at": n.created_at.isoformat() + "Z" if n.created_at else None,
            "read": n.read_at is not None,
        })
    return out


def unread_count(db: Session, user: User) -> int:
    return (
        db.query(func.count(CommentNotification.id))
        .filter(CommentNotification.user_id == user.id)
        .filter(CommentNotification.read_at.is_(None))
        .scalar()
    ) or 0


def mark_all_read(db: Session, user: User) -> int:
    """Mark every unread notification read. Returns the count marked."""
    now = datetime.utcnow()
    marked = (
        db.query(CommentNotification)
        .filter(CommentNotification.user_id == user.id)
        .filter(CommentNotification.read_at.is_(None))
        .update({CommentNotification.read_at: now})
    )
    db.commit()
    return marked
