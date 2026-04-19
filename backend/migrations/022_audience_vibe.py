"""Audience Vibe — the constitutional amendment mechanism.

A separate, persistent metric living alongside the official compass charge for
every song. The compass reads the lyrics. Audience Vibe reads the people. The
gap between them IS the data.

Three tables:

  audience_vibe_needles
    One row per (song_source, song_id). Carries the current persistent vibe
    value plus cached counters of all-time pushes. The needle is persistent —
    it never resets automatically.

  audience_vibe_pushes
    Append-only timestamped log of every push. Unique constraint on
    (song_source, song_id, device_id, push_year) enforces "one push per device
    per song per calendar year." user_id is reserved for future real-account
    auth; nullable for now (device_id-only gating).

  audience_vibe_review_cases
    Created when abs(vibe.value - song.charge) exceeds the configured threshold.
    Admin reviews the case and decides whether to trigger a public-interest
    recalibration. Only one open case per song at a time.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS audience_vibe_needles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_source VARCHAR(20) NOT NULL,
            song_id INTEGER NOT NULL,
            current_value INTEGER NOT NULL DEFAULT 0,
            pushes_up_total INTEGER NOT NULL DEFAULT 0,
            pushes_down_total INTEGER NOT NULL DEFAULT 0,
            last_push_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            UNIQUE(song_source, song_id)
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_vibe_needles_song "
        "ON audience_vibe_needles(song_source, song_id)"
    ))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS audience_vibe_pushes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_source VARCHAR(20) NOT NULL,
            song_id INTEGER NOT NULL,
            direction INTEGER NOT NULL,
            user_id INTEGER,
            device_id TEXT,
            ip_address TEXT,
            push_year INTEGER NOT NULL,
            pushed_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            UNIQUE(song_source, song_id, device_id, push_year)
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_vibe_pushes_song_year "
        "ON audience_vibe_pushes(song_source, song_id, push_year)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_vibe_pushes_at "
        "ON audience_vibe_pushes(pushed_at DESC)"
    ))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS audience_vibe_review_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_source VARCHAR(20) NOT NULL,
            song_id INTEGER NOT NULL,
            compass_charge INTEGER,
            compass_color VARCHAR(20),
            vibe_value INTEGER NOT NULL,
            gap INTEGER NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            admin_notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            resolved_at DATETIME
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_vibe_cases_song "
        "ON audience_vibe_review_cases(song_source, song_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_vibe_cases_status "
        "ON audience_vibe_review_cases(status, created_at DESC)"
    ))
