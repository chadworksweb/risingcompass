"""Optional first/last name on rc_subscribers.

The subscribe form now asks for a name alongside the email, but ONLY the email
is required, and that asymmetry is the whole design: an address is what the list
is for, a name is a courtesy that makes the digest able to greet someone. So
both columns are nullable with no default and no backfill -- every row captured
before this migration is a subscriber with no name given, which is a true
statement about them, not missing data to be repaired.

Nothing reads these yet beyond the admin list. They are captured now so the name
exists when a greeting is written, rather than being asked for later from people
who already subscribed.

Additive and nullable, so it is metadata-only on Postgres (no table rewrite, no
lock of consequence) and older code that does not know the columns keeps working
against the same table. create_all() builds them from the RcSubscriber model on
fresh installs; this is the explicit, idempotent add for existing databases.
PG-compatible.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE rc_subscribers ADD COLUMN IF NOT EXISTS first_name TEXT"
    ))
    conn.execute(text(
        "ALTER TABLE rc_subscribers ADD COLUMN IF NOT EXISTS last_name TEXT"
    ))
