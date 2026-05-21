"""Public Participation Phase 4 -- motion_arguments table.

Backs the Deliberation Chamber at /motion-desk/deliberation-chamber/{id}/.
Posts are the structured units of deliberation on an in_deliberation
motion. Five typed post kinds (argument_for, argument_against, rebuttal,
citation, clarification). 2-level depth: top-level posts + flat
rebuttals -- a rebuttal MUST have a parent_id pointing at another post
on the same motion; non-rebuttal posts MUST have parent_id NULL. That
shape is enforced in the chamber service, not in the schema (SQLite
can't express the cross-row check cleanly).

Tier 2 (id_verified) required to post. Anyone can read. Threads remain
public after the motion resolves; the room becomes read-only at that
point (enforced by the service on POST against motion.status).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS motion_arguments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            motion_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            post_type TEXT NOT NULL,
            parent_id INTEGER,
            summary TEXT NOT NULL,
            body TEXT NOT NULL,
            citations TEXT,
            created_at DATETIME NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (motion_id) REFERENCES motions(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (parent_id) REFERENCES motion_arguments(id) ON DELETE SET NULL
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_motion_arguments_motion_created "
        "ON motion_arguments(motion_id, created_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_motion_arguments_motion_type "
        "ON motion_arguments(motion_id, post_type)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_motion_arguments_parent "
        "ON motion_arguments(parent_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_motion_arguments_user "
        "ON motion_arguments(user_id, created_at DESC)"
    ))
