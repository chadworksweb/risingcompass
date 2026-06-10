"""Add songs.origin_chart -- the chart a song FIRST surfaced on (Build 7).

origin_chart = the chart_source of the song's earliest `chart_reading`
song_ingestions row; stamped once at first chart appearance, immutable, NULL
for non-chart births (lyrical_charger / terminal / editorial). It is the
first-class, queryable form of a fact previously buried in
song_ingestions.detail JSON. Shazam / YouTube / iTunes appearances create NO
chart_appearance row (excluded from the charge aggregate), so song_ingestions
was the sole provenance for exactly the social-discovery charts the thesis
cares about -- this lifts that signal onto the song itself.

Backfill derives origin from the earliest chart_reading ingestion per song.
Legacy rows whose created_at predates the column default sort last (best-effort
ordering); rows with non-JSON detail are skipped by the LIKE guard.

PG-compatible (063+). Base.metadata.create_all() builds the column on fresh
installs from the model in app/models.py.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE songs ADD COLUMN IF NOT EXISTS origin_chart TEXT"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_songs_origin_chart ON songs(origin_chart)"
    ))
    # Backfill: the earliest chart_reading ingestion per song -> its chart_source.
    # DISTINCT ON (song_id) with the matching leading ORDER BY column picks the
    # first row per song. detail is TEXT holding JSON (json.dumps at write), so
    # cast to jsonb to read chart_source; the LIKE guard skips any non-JSON row.
    conn.execute(text("""
        UPDATE songs s
        SET origin_chart = sub.chart_source
        FROM (
            SELECT DISTINCT ON (si.song_id)
                   si.song_id,
                   (si.detail::jsonb ->> 'chart_source') AS chart_source
            FROM song_ingestions si
            WHERE si.method = 'chart_reading'
              AND si.detail LIKE '{%'
            ORDER BY si.song_id, si.created_at ASC NULLS LAST, si.id ASC
        ) sub
        WHERE s.id = sub.song_id
          AND s.origin_chart IS NULL
          AND sub.chart_source IS NOT NULL
    """))
