"""Public Participation Phase 1.3 -- Lobby comments + reports + moderation log.

Three tables land together because they're all required for the Lobby
posting + reporting + moderation loop to function end-to-end:

  comments           -- the comment itself; polymorphic target by
                        (target_type, target_source, target_id)
  comment_reports    -- one row per (reporter, comment); 3 distinct
                        reporters trigger auto-hide per build plan 1.7
  moderation_events  -- append-only audit of every admin action (hide,
                        unhide, cooldown, ban) for forensics + a future
                        admin-facing changelog

Threading: parent_id chains the reply, thread_root_id points at the
top-level ancestor (self for top-level rows). That keeps thread fetch
O(1) per thread and makes the 2-level depth cap easy to enforce -- a
reply can only be filed when parent.parent_id IS NULL. No deep nesting
to flatten on read.

hidden_at vs deleted_at -- distinct on purpose:
  deleted_at -> author chose to remove their own comment; render as
                "[deleted]" placeholder, preserve thread structure
  hidden_at  -> admin (or auto-hide trigger) suppressed it for policy;
                render as "[hidden -- under review]" with the reason
                visible to the author per the no-shadow-ban ethic
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id INTEGER NOT NULL,
            target_type TEXT NOT NULL,
            target_source TEXT,
            target_id INTEGER NOT NULL,
            parent_id INTEGER,
            thread_root_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            content_length INTEGER NOT NULL,
            edited_at DATETIME,
            deleted_at DATETIME,
            hidden_at DATETIME,
            hidden_reason TEXT,
            created_at DATETIME NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (author_id) REFERENCES users(id),
            FOREIGN KEY (parent_id) REFERENCES comments(id),
            FOREIGN KEY (thread_root_id) REFERENCES comments(id)
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_comments_target "
        "ON comments(target_type, target_source, target_id, created_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_comments_thread "
        "ON comments(thread_root_id, created_at)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_comments_author "
        "ON comments(author_id, created_at DESC)"
    ))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS comment_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id INTEGER NOT NULL,
            reporter_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            resolved_at DATETIME,
            resolved_by_admin_id INTEGER,
            created_at DATETIME NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (comment_id) REFERENCES comments(id),
            FOREIGN KEY (reporter_id) REFERENCES users(id),
            UNIQUE (comment_id, reporter_id)
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_comment_reports_status "
        "ON comment_reports(status, created_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_comment_reports_comment "
        "ON comment_reports(comment_id, status)"
    ))

    # Audit + a forensic record of every consequential moderation action.
    # Includes auto-hide events so we can distinguish user-triggered vs
    # admin-triggered hides without a separate flag.
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS moderation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            actor_admin_id INTEGER,
            target_user_id INTEGER,
            target_comment_id INTEGER,
            reason TEXT,
            details TEXT,
            created_at DATETIME NOT NULL DEFAULT (datetime('now'))
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_moderation_events_target_user "
        "ON moderation_events(target_user_id, created_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_moderation_events_target_comment "
        "ON moderation_events(target_comment_id, created_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_moderation_events_action "
        "ON moderation_events(action, created_at DESC)"
    ))
