"""Stamp the resolved song identity onto chart_snapshots.

`chart_snapshots` has always stored a chart slot as raw strings (date,
chart_source, position, title, artist) and resolved the per-song charge LIVE at
render time by case-insensitive title+artist lookup. That works for painting one
chart's own panel, where a miss just means one row renders without a tier. It
does not work for anything that has to UNION across charts, because the same song
arrives spelled differently from every feeder: YouTube hands us
`ARTIST - TITLE (Official Video)` where Spotify hands us `TITLE` / `ARTIST`, and a
string match will silently treat those as two different songs.

Measured on prod 2026-08-16, exact lowercased title+artist matching against
`songs` over the trailing 14 days of published snapshots:

    Shazam    258 / 280 rows matched  (92%)
    YouTube   259 / 280 rows matched  (93%)
    iTunes    242 / 280 rows matched  (86%)

By contrast `reading_songs` (the Spotify daily reading) carries `song_id` on 20
of 20 rows every single day. The daily reading has identity; the snapshot charts
throw it away.

That last part is the galling bit: the identity work is ALREADY DONE. Every chart
draft resolves each song through `song_identity.resolve_song_identity` during
calibration and stores the answer on `agent_draft_songs.song_id`. Then approval
rebuilds the snapshot from those same draft rows, drops the column on the floor,
and deletes the draft (`_cleanup_day_drafts`). Prod currently holds 3 rows in
`agent_drafts`, all rejected, the newest from 2026-06-13, so for every published
chart day the resolved identity is simply gone.

So this is not new resolution work, it is keeping an answer we already computed.
`agent.approve_draft` now carries `song.song_id` onto each ChartSnapshot row the
same way the daily branch already carries it onto ReadingSong.

  song_id -- the unified `songs.id` this chart slot refers to. Nullable, because
             a fetch-time (unpublished) row is written before any draft exists
             and a historical row predates the stamp. ON DELETE SET NULL to match
             `reading_songs.song_id`: a merged or removed song must not take a
             chart's historical record with it, since the slot is a true
             statement about what charted that day regardless.

NULL is a meaningful value here and stays queryable: it means "this slot has no
confirmed identity", which is exactly the set a backfill or an audit wants. Any
consumer that unions across charts must treat NULL as ineligible rather than
falling back to string matching, or it reintroduces the bug this fixes.

Additive and nullable, so it is metadata-only on Postgres (no table rewrite). The
FK validates instantly because every existing row is NULL. The index is a plain
CREATE INDEX rather than CONCURRENTLY: the table is small (a few thousand rows)
and the runner wraps each migration in a transaction, which CONCURRENTLY cannot
run inside.

create_all() builds this on fresh installs from the ChartSnapshot model; this is
the explicit, idempotent add for existing databases. Backfill of historical rows
is a separate, optional pass (`scripts/backfill_chart_snapshot_song_id.py`), not
done here. PG-compatible.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE chart_snapshots ADD COLUMN IF NOT EXISTS song_id INTEGER"
    ))
    # Separate from the ADD COLUMN so a re-run against a database that already
    # has the column still reaches the constraint and the index.
    conn.execute(text(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_chart_snapshots_song_id'
            ) THEN
                ALTER TABLE chart_snapshots
                    ADD CONSTRAINT fk_chart_snapshots_song_id
                    FOREIGN KEY (song_id) REFERENCES songs(id) ON DELETE SET NULL;
            END IF;
        END $$;
        """
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_chart_snapshots_song_id "
        "ON chart_snapshots(song_id)"
    ))
