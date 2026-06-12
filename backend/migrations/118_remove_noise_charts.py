"""Remove three phantom Tier-1 chart slugs from the compass charge.

billboard_200, spotify, and spotify_global_daily were all members of
AGGREGATING_CHART_SLUGS (eligible to feed the compass charge) but none had a
real live feed:
  - billboard_200        -> 0 chart_appearances anywhere
  - spotify_global_daily -> 7 appearances, ALL year 2026 (live-year)
  - spotify              -> 3 appearances: two year-1975 + one 2026

The daily charge reads reading_songs (100% spotify_top50_usa) and the live-year
list reads ReadingSong, so none of these three ever fed the DAILY charge. The
only real number any of them touched is the historical (<=2025) aggregate, via
the two year-1975 spotify rows -- and Spotify did not exist in 1975, so those
are legacy mis-stamps.

So: re-stamp + repoint those two 1975 rows onto Billboard Year-End Hot 100 (the
<=2025 aggregate stays byte-for-byte identical), delete the live-year strays,
NULL any leftover origin_chart pointing at a removed slug, then drop the three
charts rows. After this, AGGREGATING_CHART_SLUGS holds only the two charts that
actually feed a charge: spotify_top50_usa (daily) + billboard_yearend_hot100
(historical).

create_all() + migration 081 seed these three charts on a fresh install; this
removes them afterward, so a fresh DB ends up without them too. Every statement
is WHERE-guarded, so a re-run / fresh install is a no-op.

PG-compatible (063+).
"""

from sqlalchemy import text


def up(conn):
    # A. The songs whose origin is stamped 'spotify' AND that have a historical
    #    (<=2025) spotify appearance are the legacy mis-stamps. Move their origin
    #    to the Billboard Year-End key the rest of the backfill uses
    #    ('billboard_hot_100' -> label "Billboard Year-End Hot 100"). Done BEFORE
    #    the repoint so it keys precisely on the spotify appearance.
    conn.execute(text("""
        UPDATE songs SET origin_chart = 'billboard_hot_100'
        WHERE origin_chart = 'spotify'
          AND id IN (
              SELECT ca.song_id FROM chart_appearances ca
              WHERE ca.chart_id = (SELECT id FROM charts WHERE slug = 'spotify')
                AND ca.year <= 2025
          )
    """))

    # B. Repoint those historical spotify appearances onto Billboard Year-End
    #    Hot 100 so the <=2025 charge aggregate is unchanged. The NOT EXISTS
    #    guard keeps the chart_appearances unique constraint
    #    (song_id, chart_id, year, position, position_letter) safe if an
    #    identical Billboard row somehow already exists.
    conn.execute(text("""
        UPDATE chart_appearances ca
        SET chart_id = (SELECT id FROM charts WHERE slug = 'billboard_yearend_hot100')
        WHERE ca.chart_id = (SELECT id FROM charts WHERE slug = 'spotify')
          AND ca.year <= 2025
          AND NOT EXISTS (
              SELECT 1 FROM chart_appearances x
              WHERE x.song_id = ca.song_id
                AND x.chart_id = (SELECT id FROM charts WHERE slug = 'billboard_yearend_hot100')
                AND x.year = ca.year
                AND x.position IS NOT DISTINCT FROM ca.position
                AND x.position_letter IS NOT DISTINCT FROM ca.position_letter
          )
    """))

    # C. Delete every remaining appearance under the three noise slugs: the
    #    live-year 2026 strays + any historical spotify row a duplicate blocked
    #    from repointing in B.
    conn.execute(text("""
        DELETE FROM chart_appearances
        WHERE chart_id IN (
            SELECT id FROM charts
            WHERE slug IN ('spotify', 'spotify_global_daily', 'billboard_200')
        )
    """))

    # D. Any song still pointing its origin at a removed slug loses that
    #    provenance (honest NULL = no recorded chart origin). The repointed 1975
    #    songs were already moved in A, so they are not nulled here.
    conn.execute(text("""
        UPDATE songs SET origin_chart = NULL
        WHERE origin_chart IN ('spotify', 'spotify_global_daily')
    """))

    # E. Drop the three charts rows now that nothing references them
    #    (chart_appearances is the only FK into charts, cleared above).
    conn.execute(text("""
        DELETE FROM charts
        WHERE slug IN ('spotify', 'spotify_global_daily', 'billboard_200')
    """))
