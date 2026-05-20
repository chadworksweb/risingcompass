"""Drop the thread_root_id -> comments.id FK on the comments table.

Why: the insert flow writes a placeholder 0 first (the row's own id isn't
known until flush), then patches thread_root_id = id immediately after.
SQLite enforces FKs at row-insert time, so the placeholder violates the
constraint and the whole transaction aborts with SQLITE_CONSTRAINT.

thread_root_id is a denormalized lookup helper -- it points at the
top-level ancestor of a reply for O(1) thread fetches. The structural
integrity FK is parent_id, which we keep. A bad thread_root_id at worst
just means a row doesn't surface in a thread query -- same failure mode
as a NULL.

SQLite has no DROP CONSTRAINT, so we recreate the table without the FK
and copy rows across. Indexes are recreated. Idempotent against the
no-rows-yet local DB and safe on prod (preserves any existing rows).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS comments_new (
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
            FOREIGN KEY (parent_id) REFERENCES comments_new(id)
        )
    """))
    conn.execute(text("""
        INSERT INTO comments_new (
            id, author_id, target_type, target_source, target_id, parent_id,
            thread_root_id, content, content_length, edited_at, deleted_at,
            hidden_at, hidden_reason, created_at
        )
        SELECT
            id, author_id, target_type, target_source, target_id, parent_id,
            thread_root_id, content, content_length, edited_at, deleted_at,
            hidden_at, hidden_reason, created_at
        FROM comments
    """))
    conn.execute(text("DROP TABLE comments"))
    conn.execute(text("ALTER TABLE comments_new RENAME TO comments"))

    # Recreate indexes (DROP TABLE took them with it).
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
