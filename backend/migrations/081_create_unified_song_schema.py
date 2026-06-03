"""Unified song-entity renovation -- core new tables (Phase 1, additive/inert).

Creates the unified `songs` table (the atomic unit AND the whole Library), the
`charts` registry, `chart_appearances` (charting becomes a derived role), the
`song_ingestions` log dimension, and `song_id_map` (the migration-only old->new
id mapping consumed by scripts/unify_songs.py in Phase 2).

Nothing reads these yet -- Phase 1 is inert. Reads/writes flip at the Phase 3
cutover; the four legacy song tables are dropped only in Phase 5 after
verification. PG-compatible (063+); Base.metadata.create_all() builds the same
shape on fresh installs from the models in app/models.py.

See RISING-COMPASS-SONG-ENTITY-RENOVATION.md (build binder) for the full plan.
"""

from sqlalchemy import text


def up(conn):
    # --- songs: unified atomic entity = the entire Library -------------------
    # Holds the single canonical calibration (the column set is identical across
    # the four legacy tables today). Display title/artist preserved verbatim;
    # identity is the normalized canonical_key (UNIQUE) -- the fix the missing
    # migration-037 functional index never delivered on PG.
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS songs (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            rubric_color TEXT,
            charge_value INTEGER,
            charge_summary TEXT,
            contaminated BOOLEAN DEFAULT FALSE,
            contamination_note TEXT,
            dogma_referenced BOOLEAN DEFAULT FALSE,
            dogma_note TEXT,
            instrumental BOOLEAN DEFAULT FALSE,
            confidence DOUBLE PRECISION,
            effects_prose TEXT,
            societal_effects_prose TEXT,
            societal_prose_generated_at TIMESTAMP,
            societal_prose_model TEXT,
            prior_effects_prose TEXT,
            prior_societal_effects_prose TEXT,
            prior_societal_prose_generated_at TIMESTAMP,
            prior_societal_prose_model TEXT,
            deadpan_line TEXT,
            topics TEXT,
            topic_audit TEXT,
            activations TEXT,
            calibration_failed BOOLEAN DEFAULT FALSE,
            message_analysis TEXT,
            expression_analysis TEXT,
            intention_analysis TEXT,
            album_id INTEGER REFERENCES album_deep_dives(id),
            track_number INTEGER,
            canonical_calibration_method TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_songs_canonical_key "
        "ON songs(canonical_key)"
    ))

    # --- charts: chart definitions (extensible; multi-chart from day one) -----
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS charts (
            id SERIAL PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            cadence TEXT NOT NULL DEFAULT 'annual',
            provider TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))

    # --- chart_appearances: a song on a chart at a time ----------------------
    # One row per legacy compass_songs charting row. A song charting in 3 years
    # has 3 appearances on ONE songs entity. position_letter defaults to '' so
    # the idempotency UNIQUE works identically on PG and fresh installs (PG
    # treats NULLs as distinct in unique indexes).
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS chart_appearances (
            id SERIAL PRIMARY KEY,
            song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
            chart_id INTEGER NOT NULL REFERENCES charts(id),
            year INTEGER,
            period DATE,
            position INTEGER,
            position_letter TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_chart_appearance "
        "ON chart_appearances(song_id, chart_id, year, position, position_letter)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_chart_appearance_song "
        "ON chart_appearances(song_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_chart_appearance_chart_year "
        "ON chart_appearances(chart_id, year)"
    ))

    # --- song_ingestions: how each song entered (logged dimension) -----------
    # method: chart_reading | lyrical_charger | api_client | terminal |
    #         editorial | stream. New home for submitted_songs.ip_address.
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS song_ingestions (
            id SERIAL PRIMARY KEY,
            song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
            method TEXT NOT NULL,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            api_client_id INTEGER,
            ip_address VARCHAR(45),
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_song_ingestion_song "
        "ON song_ingestions(song_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_song_ingestion_method "
        "ON song_ingestions(method)"
    ))

    # --- song_id_map: migration-only old (source,id) -> new songs.id ----------
    # Permanent through Phase 5 as the reverse-mapping rollback safety net.
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS song_id_map (
            old_source TEXT NOT NULL,
            old_id INTEGER NOT NULL,
            new_song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
            canonical_key TEXT NOT NULL,
            PRIMARY KEY (old_source, old_id)
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_song_id_map_new "
        "ON song_id_map(new_song_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_song_id_map_key "
        "ON song_id_map(canonical_key)"
    ))

    # --- seed charts ----------------------------------------------------------
    # Slugs reconcile the actual distinct compass_songs.chart_source values
    # (Phase 0 enumeration) with CHART_SOURCES + CHART_REGISTRY. The
    # chart_source -> chart slug mapping lives in app/constants.py
    # (CHART_SOURCE_TO_CHART_SLUG); 'manual'/'backfill_console' are non-chart.
    conn.execute(text("""
        INSERT INTO charts (slug, label, cadence, provider) VALUES
            ('billboard_yearend_hot100', 'Billboard Year-End Hot 100', 'annual', 'billboard'),
            ('billboard_200', 'Billboard 200', 'annual', 'billboard'),
            ('spotify_top50_usa', 'Spotify Top 50 - USA', 'daily', 'spotify'),
            ('spotify_viral50_usa', 'Spotify Viral 50 - USA', 'daily', 'spotify'),
            ('spotify_global_daily', 'Spotify Global Daily', 'daily', 'spotify'),
            ('spotify', 'Spotify (legacy)', 'daily', 'spotify')
        ON CONFLICT (slug) DO NOTHING
    """))
