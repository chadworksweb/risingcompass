"""Rename ApiClient.plan_tier default 'trial' -> 'free' (M6).

Drops the legacy 'trial' vocabulary in line with the metering core: there
is no trial concept anywhere in the consumer or B2B billing model -- the
two paths are 'free' (default, anonymous + signed-out + non-paying) and
the paid tiers (plus / pro for users, plus / pro / internal for clients).

Updates:
  - column default in PG (ALTER TABLE ... SET DEFAULT 'free')
  - any existing rows still on 'trial' -> 'free' (idempotent)

The model + dataclass defaults and the admin form placeholder were renamed
in the same M6 commit so future inserts default to 'free' too.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE api_clients ALTER COLUMN plan_tier SET DEFAULT 'free'"
    ))
    conn.execute(text(
        "UPDATE api_clients SET plan_tier = 'free' WHERE plan_tier = 'trial'"
    ))
