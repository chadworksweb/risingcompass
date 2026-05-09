"""Lyrical Charger temporarily-unavailable mode.

Two new tables:
  - system_flags: generic key/value flag store; first row is
    'lyrical_charger.disabled' (default 'false').
  - lyrical_charger_subscribers: emails captured while LC is disabled
    so we can notify them when it comes back online.

Added because the Anthropic credit balance can deplete and leave LC
calibration paths returning 500s. The flag lets us flip LC into a
clean unavailable state with an email subscribe form, and notify
subscribers manually from the admin dashboard once we top up.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS system_flags ("
        "  key TEXT PRIMARY KEY,"
        "  value TEXT NOT NULL,"
        "  updated_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS lyrical_charger_subscribers ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  email TEXT NOT NULL UNIQUE,"
        "  created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        "  notified_at TEXT"
        ")"
    ))
    conn.execute(text(
        "INSERT OR IGNORE INTO system_flags (key, value) VALUES ('lyrical_charger.disabled', 'false')"
    ))
