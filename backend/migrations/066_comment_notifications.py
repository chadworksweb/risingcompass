"""Lobby comment notifications (Phase 1.5).

Notify a user when someone replies to their comment or @mentions their
handle in a comment. v1 surfaces on the account page (unread count + list
+ mark-all-read). No header bell yet.

  user_id    -- recipient (the replied-to / mentioned user)
  type       -- 'reply' | 'mention'
  comment_id -- the comment that triggered it (the reply/mention itself)
  actor_id   -- who caused it (the comment's author)
  read_at    -- NULL = unread

PG-compatible (063+).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        """
        CREATE TABLE IF NOT EXISTS comment_notifications (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            comment_id INTEGER NOT NULL,
            actor_id INTEGER NOT NULL,
            read_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT now()
        )
        """
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_comment_notifications_user_unread "
        "ON comment_notifications (user_id, read_at)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_comment_notifications_user_created "
        "ON comment_notifications (user_id, created_at DESC)"
    ))
