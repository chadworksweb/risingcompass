"""Create alltime_stream_albums -- the Most-Streamed Albums of All Time chart
(Spotify GLOBAL lifetime streams, top 100).

The streaming-era twin of the RIAA best-sellers board: it surfaces the modern
albums (Bad Bunny, SZA, Olivia Rodrigo) that the physical-sales list misses
entirely. Current-state table, refreshed MONTHLY by scraping kworb.net. Like
the songs board, the calibration columns are denormalized off a matching charged
Release at refresh time (auto-link), so the public page reads one table.

PG-compatible (063+). create_all() handles fresh installs; this is the
idempotent prod path.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS alltime_stream_albums (
            id SERIAL PRIMARY KEY,
            rank INTEGER NOT NULL,
            album_title TEXT NOT NULL,
            artist TEXT NOT NULL,
            total_streams BIGINT,
            daily_streams INTEGER,
            release_id INTEGER REFERENCES releases(id) ON DELETE SET NULL,
            rubric_color VARCHAR(20),
            charge_value INTEGER,
            charge_summary TEXT,
            deadpan_line TEXT,
            topics TEXT,
            artist_slug TEXT,
            release_slug TEXT,
            non_music BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_alltime_stream_albums_rank "
        "ON alltime_stream_albums(rank)"
    ))
