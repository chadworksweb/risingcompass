"""Create alltime_stream_songs -- the Most-Streamed Songs of All Time chart
(Spotify GLOBAL lifetime streams, top 100).

Current-state table (the chart IS these 100 rows, upserted in place), NOT a
dated snapshot -- deliberately separate from chart_snapshots / the daily-reading
pipeline. Refreshed monthly by scraping kworb.net. Stream rank + counts are the
real chart data; the calibration columns are denormalized off the unified songs
row on a cache hit so the public page reads one table with no join.

PG-compatible (063+). create_all() handles fresh installs; this is the
idempotent prod path.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS alltime_stream_songs (
            id SERIAL PRIMARY KEY,
            rank INTEGER NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            total_streams BIGINT,
            daily_streams INTEGER,
            song_id INTEGER REFERENCES songs(id) ON DELETE SET NULL,
            rubric_color VARCHAR(20),
            charge_value INTEGER,
            deadpan_line TEXT,
            topics TEXT,
            song_slug TEXT,
            artist_slug TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_alltime_stream_songs_rank "
        "ON alltime_stream_songs(rank)"
    ))
