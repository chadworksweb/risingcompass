"""Add admin_users.timezone -- per-admin display timezone for the admin area.

Times are stored in UTC; this column is purely the per-admin RENDER zone, used
by the admin-area timezone picker (the whole admin UI reformats client-side to
this zone). Default America/New_York (ET). Public UI stays user-local; code and
billing stay UTC -- this is admin display only.

PG-compatible (063+). Base.metadata.create_all() picks the column up on fresh
installs from the updated AdminUser model; this migration is the idempotent path
for the existing prod DB.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS timezone "
        "VARCHAR(64) NOT NULL DEFAULT 'America/New_York'"
    ))
