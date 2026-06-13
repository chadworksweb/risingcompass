"""Remove the dead Spotify Viral 50 - USA chart.

spotify_viral50_usa was the ORIGINAL second homepage panel (migration 045),
seeded as a charts row in the unified baseline (migration 081). Spotify retired
its Viral 50 charts in May 2026 (the playlist 404s), so the panel slot was
reskinned to the iTunes Download Chart and the Viral 50 was abandoned in place:
a charts row, ~16 stranded chart_appearances, plus any unpublished
chart_snapshots rows left by the old panel.

It was NEVER a member of AGGREGATING_CHART_SLUGS, so unlike the migration-118
noise charts NONE of its appearances ever fed any charge (daily or historical) --
there is nothing to repoint. This just deletes the strays and drops the row.
Separate from migration 118 (which handled billboard_200 / spotify /
spotify_global_daily); same nuke shape, no repoint step.

Every statement is WHERE-guarded on the slug, so a re-run / fresh install (where
081 seeds the row, then this removes it) is a no-op.

PG-compatible (063+).
"""

from sqlalchemy import text


def up(conn):
    # A. Delete the stranded chart_appearances under the dead slug. None feed a
    #    charge (slug was never in AGGREGATING_CHART_SLUGS), so no repoint.
    conn.execute(text("""
        DELETE FROM chart_appearances
        WHERE chart_id = (SELECT id FROM charts WHERE slug = 'spotify_viral50_usa')
    """))

    # B. Delete any leftover panel snapshot rows (the old second-panel feed;
    #    all unpublished after the iTunes reskin). chart_snapshots keys on the
    #    chart_source slug, not a charts FK.
    conn.execute(text("""
        DELETE FROM chart_snapshots
        WHERE chart_source = 'spotify_viral50_usa'
    """))

    # C. Any song whose recorded origin points at the dead slug loses that
    #    provenance (honest NULL = no live chart origin).
    conn.execute(text("""
        UPDATE songs SET origin_chart = NULL
        WHERE origin_chart = 'spotify_viral50_usa'
    """))

    # D. Drop the charts row now that nothing references it (chart_appearances,
    #    the only FK into charts, was cleared in A).
    conn.execute(text("""
        DELETE FROM charts WHERE slug = 'spotify_viral50_usa'
    """))
