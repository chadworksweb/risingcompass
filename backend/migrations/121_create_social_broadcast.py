"""Build 6 social broadcaster -- the dedup/audit ledger + the rendered-card store.

Two tables behind the automated own-account social broadcast machine
(RISING-COMPASS-HOCKEY-STICK-PLAN.md Build 6). The cron selects the day's
top-3 trending calibrated verdicts + the daily-aggregate reading, renders a
1080x1080 card for each (Playwright screenshot of frontend/cards/), pushes them
to RC's Buffer queue (fan-out to all connected platforms), and records the
result here so the same song/reading is never broadcast twice.

- social_cards: the rendered PNG for one broadcast item (one card, shared across
  every platform it fans out to). Served publicly at /api/social/card/{token}.png
  so Buffer can fetch the media by URL. token is the unguessable public handle.
- social_posts: one row per (item, platform). dedup_key (UNIQUE) is the
  idempotency + dedup key -- 'song:{song_id}:{platform}' or
  'reading:{date}:{platform}' -- so a re-run never double-posts and selection can
  cheaply exclude already-broadcast items. status tracks the lifecycle
  (prepared|dark|queued|posted|skipped|error); 'dark' = built + ledgered but not
  pushed because Buffer is not configured yet (the subsystem ships dark).

PG-compatible (063+). Base.metadata.create_all() builds both on fresh installs
from the models in app/models.py; this creates them on existing DBs. Idempotent
(CREATE TABLE / INDEX IF NOT EXISTS).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS social_cards (
            id          SERIAL PRIMARY KEY,
            token       TEXT NOT NULL UNIQUE,
            scope       TEXT NOT NULL,
            ref         TEXT NOT NULL,
            png         BYTEA NOT NULL,
            content_type TEXT NOT NULL DEFAULT 'image/png',
            created_at  TIMESTAMP DEFAULT (now() at time zone 'utc')
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS social_posts (
            id              SERIAL PRIMARY KEY,
            scope           TEXT NOT NULL,
            song_id         INTEGER REFERENCES songs(id) ON DELETE SET NULL,
            reading_date    DATE,
            platform        TEXT NOT NULL,
            dedup_key       TEXT NOT NULL UNIQUE,
            post_text       TEXT NOT NULL,
            card_token      TEXT,
            charge_value    INTEGER,
            tier            TEXT,
            trending_source TEXT,
            post_external_id TEXT,
            status          TEXT NOT NULL DEFAULT 'prepared',
            error           TEXT,
            created_at      TIMESTAMP DEFAULT (now() at time zone 'utc'),
            posted_at       TIMESTAMP
        )
    """))

    # Selection dedup reads by song / reading_date filtered to live statuses.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_social_posts_song ON social_posts (song_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_social_posts_reading_date ON social_posts (reading_date)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_social_posts_created_at ON social_posts (created_at)"
    ))
