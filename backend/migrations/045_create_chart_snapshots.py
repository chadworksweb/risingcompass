"""Per-chart daily song snapshots.

One row per (date, chart_source, position). Designed to scale to any number
of registered chart sources — the canonical daily reading still flows
through daily_readings/reading_songs untouched. This table is the lighter
"just store today's chart top 20 so we can show a panel" mechanism, with
RC charge values looked up live against compass_songs at render time.

First two charts in flight:
  - spotify_top50_usa     (mirrors the daily reading source for parity)
  - spotify_viral50_usa   (new second panel)

Future: spotify_top50_global, apple_music_top100, billboard_hot100, ...
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS chart_snapshots (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            date          DATE NOT NULL,
            chart_source  TEXT NOT NULL,
            position      INTEGER NOT NULL,
            title         TEXT NOT NULL,
            artist        TEXT NOT NULL,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, chart_source, position)
        )
    """))

    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_chart_snapshots_source_date "
        "ON chart_snapshots(chart_source, date DESC)"
    ))
