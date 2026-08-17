"""The Unified Charge Chart: stored readings, the ranked chart, the weight vector.

Three tables. The composer (services/unified_chart.py) is pure and computes on
demand; this is where its output is kept so the Calendar can paint a day, the
broadcaster can post a specific number, and a published reading stays the number
a reader was shown.

WHY PERSIST AT ALL, RATHER THAN COMPOSE ON READ

Compute-on-read was considered and rejected. The unified reading recomposes as
late constituents get approved through the day, and an editorial is written
against a specific figure (see the scope, 6.3 and 8.6). A headline that silently
changes under a reader is worse than one that visibly updates, and there has to
be a row to hang the editorial and the publish flag on.

WHY A DEDICATED WEIGHT TABLE INSTEAD OF `charts.unified_weight`

The scope originally specified a column on `charts`. That does not work: `charts`
holds exactly TWO rows on prod (billboard_yearend_hot100 and spotify_top50_usa).
iTunes, Shazam, and YouTube have no row there at all, because `charts` exists to
be the FK target for `chart_appearances` and only aggregating charts ever get
appearances. Seeding three rows into it to carry a weight would overload a table
whose meaning is "a chart songs can have appearances on" and would put rows in
front of every consumer that joins it.

`unified_chart_weights` is read by one module and means one thing. It also gives
the weight vector its own audit surface, which matters because the weights are
the single arbitrary knob in the whole design and are meant to be pre-registered
and published (scope section 5).

Seeded at 1.0 across the four constituents: the pre-registered v1. Equal not
because every platform deserves equal say, but because no other split can yet be
defended with evidence, and a weighting invented to produce a preferred number is
the exact attack this design has to survive.

NOTE ON GRANTS: RC runs on DigitalOcean Managed Postgres with a single
application role, not Supabase, so these carry no GRANT block and no RLS enable.
That rule exists for Supabase projects, where anon/authenticated lose default
grants. Adding it here would be noise. Every other RC create-table migration
(148, 150, 152) does the same.

PG-compatible. create_all() builds these on fresh installs from the models.
"""

from sqlalchemy import text


def up(conn):
    # --- the weight vector -------------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS unified_chart_weights (
            slug        TEXT PRIMARY KEY,
            weight      DOUBLE PRECISION NOT NULL DEFAULT 1.0,
            note        TEXT,
            updated_at  TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
        )
    """))
    # Idempotent seed. ON CONFLICT DO NOTHING so a re-run never stomps a weight
    # an admin has deliberately changed -- re-weighting is the whole point of the
    # table, and a migration replay must not silently undo it.
    for slug in ("spotify_top50_usa", "itunes_download_usa",
                 "shazam_top200_usa", "youtube_trending_usa"):
        conn.execute(
            text("INSERT INTO unified_chart_weights (slug, weight, note) "
                 "VALUES (:s, 1.0, 'pre-registered v1: equal weight') "
                 "ON CONFLICT (slug) DO NOTHING"),
            {"s": slug},
        )

    # --- the composed reading ----------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS unified_readings (
            id                  SERIAL PRIMARY KEY,
            date                DATE NOT NULL UNIQUE,

            compass_degree      DOUBLE PRECISION NOT NULL,
            charge_level        TEXT NOT NULL,
            contamination_count INTEGER NOT NULL DEFAULT 0,
            song_count          INTEGER NOT NULL DEFAULT 0,

            -- JSON in a TEXT column, RC's convention for per-row JSON bundles
            -- (songs.topics, songs.psyche_facts, songs.activations).
            -- sources_included: [{slug, weight, slots, eligible, coverage}]
            -- sources_excluded: [{slug, reason, ...}]
            sources_included    TEXT NOT NULL DEFAULT '[]',
            sources_excluded    TEXT NOT NULL DEFAULT '[]',
            source_count        INTEGER NOT NULL DEFAULT 0,

            -- Short stable hash of the weight vector this reading was computed
            -- under, so a published number can always be reproduced and a
            -- re-weight is visible as a change of version rather than as an
            -- unexplained shift in the series.
            weights_version     TEXT NOT NULL DEFAULT '',
            weights             TEXT NOT NULL DEFAULT '{}',

            -- The editorial IS the publication gate (scope 8.6). The reading
            -- composes automatically and goes public when the editorial lands,
            -- which keeps the prose and the number in lockstep by construction.
            editorial           TEXT,
            published           BOOLEAN NOT NULL DEFAULT FALSE,

            -- Set when a recompose changes the numbers on an ALREADY-published
            -- reading (a constituent approved or corrected late). The prose was
            -- written against the old figure, so it is flagged rather than
            -- silently republished over new numbers.
            editorial_stale     BOOLEAN NOT NULL DEFAULT FALSE,

            composed_at         TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
            published_at        TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_unified_readings_date "
        "ON unified_readings(date DESC)"
    ))

    # --- the ranked chart --------------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS unified_reading_songs (
            id             SERIAL PRIMARY KEY,
            reading_id     INTEGER NOT NULL
                           REFERENCES unified_readings(id) ON DELETE CASCADE,
            song_id        INTEGER NOT NULL
                           REFERENCES songs(id) ON DELETE CASCADE,
            position       INTEGER NOT NULL,

            -- The sum of this song's normalized rank weights across every
            -- constituent it appeared on. This IS the ranking key; `position` is
            -- its rendering.
            unified_weight DOUBLE PRECISION NOT NULL,
            -- How many constituents carried it. Denormalized from `sources` so
            -- the corroboration filter is an indexable integer, not a JSON scan.
            chart_count    INTEGER NOT NULL DEFAULT 1,
            -- JSON {slug: rank}, the per-source placement behind the weight.
            sources        TEXT NOT NULL DEFAULT '{}',

            CONSTRAINT uq_unified_reading_song UNIQUE (reading_id, song_id)
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_unified_reading_songs_reading "
        "ON unified_reading_songs(reading_id, position)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_unified_reading_songs_song "
        "ON unified_reading_songs(song_id)"
    ))
