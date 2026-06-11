"""Create alltime_albums -- the Best-Selling Albums of All Time chart
(US / RIAA certified units, top 100).

Maintained by a MANUAL annual sweep (this list changes once every few years),
no cron. An admin editor owns each row; last_reviewed_at drives a data-driven
staleness banner. Self-contained: the charge + album-level deadpan + topics are
copied onto the row from album synthesis output. release_id links the
calibrated Release for provenance.

PG-compatible (063+). create_all() handles fresh installs; this is the
idempotent prod path.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS alltime_albums (
            id SERIAL PRIMARY KEY,
            rank INTEGER NOT NULL,
            album_title TEXT NOT NULL,
            artist TEXT NOT NULL,
            certified_units TEXT,
            units_millions DOUBLE PRECISION,
            release_year INTEGER,
            release_id INTEGER REFERENCES releases(id) ON DELETE SET NULL,
            rubric_color VARCHAR(20),
            charge_value INTEGER,
            charge_summary TEXT,
            deadpan_line TEXT,
            topics TEXT,
            artist_slug TEXT,
            release_slug TEXT,
            last_reviewed_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_alltime_albums_rank "
        "ON alltime_albums(rank)"
    ))
