"""Add notification-category toggles to rc_subscribers (Hockey Stick Build 2b,
preferences pass). Three opt-out booleans, all DEFAULT true so every existing
confirmed subscriber keeps the daily reading AND is opted into the two new
categories (mirrors chadlewine's opt-out model). The preference center lets a
subscriber turn any of them off from their tokenized email link.

  pref_daily_reading      -- the existing reading digest
  pref_moments_of_notice  -- spikes / anomalies / notable shifts (admin broadcast)
  pref_config_updates     -- features / framework changes / version launches

PG-compatible (063+).
"""
from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE rc_subscribers "
        "ADD COLUMN IF NOT EXISTS pref_daily_reading BOOLEAN NOT NULL DEFAULT true"
    ))
    conn.execute(text(
        "ALTER TABLE rc_subscribers "
        "ADD COLUMN IF NOT EXISTS pref_moments_of_notice BOOLEAN NOT NULL DEFAULT true"
    ))
    conn.execute(text(
        "ALTER TABLE rc_subscribers "
        "ADD COLUMN IF NOT EXISTS pref_config_updates BOOLEAN NOT NULL DEFAULT true"
    ))
