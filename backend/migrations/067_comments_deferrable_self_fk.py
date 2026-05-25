"""Make the comments self-FKs deferrable so top-level posts work on PG.

`create_top_level` inserts a comment with thread_root_id pointing at its
own id, which isn't known until after the INSERT. The flush sets a
placeholder and patches the real id before commit. PG enforces FKs
immediately by default, so the placeholder flush raised
ForeignKeyViolation and top-level Lobby comments were broken on Postgres
(SQLite, where this was built, has FK enforcement off by default).

Fix: mark the self-referential FKs DEFERRABLE INITIALLY DEFERRED so the
check runs at COMMIT, by which point thread_root_id is valid.

PG-compatible (063+).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE comments ALTER CONSTRAINT comments_thread_root_id_fkey "
        "DEFERRABLE INITIALLY DEFERRED"
    ))
    conn.execute(text(
        "ALTER TABLE comments ALTER CONSTRAINT comments_parent_id_fkey "
        "DEFERRABLE INITIALLY DEFERRED"
    ))
