"""Backfill Console job tables.

Two-table queue + work-item schema for the unified Backfill Console
(admin UI for mass-adding songs through the standard
calibrate-then-tag SOP).

  backfill_jobs       — one row per submitted job
  backfill_job_rows   — one row per song in the job's input list

Each row tracks its own state machine independently so the worker can
pause/resume mid-job and the UI can render per-row status. Lyrics paste
lives on the row (TEXT, large) — paste-only is the v1 lyrics source per
build decision; the Musixmatch service is wired in code as elsewhere
but not flipped on by default.

Forward-only schema; no foreign keys to compass_songs/library_songs
since rows reference the result by `result_song_source` + `result_song_id`
columns (matches the polymorphic-song pattern used elsewhere in RC).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS backfill_jobs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            label           TEXT NOT NULL,
            target_table    TEXT NOT NULL,           -- 'compass' | 'library'
            passes          TEXT NOT NULL,           -- 'calibrate' | 'tag' | 'both'
            status          TEXT NOT NULL DEFAULT 'queued',
                                                    -- queued | running | paused
                                                    --        | completed | cancelled | failed
            paused_flag     INTEGER NOT NULL DEFAULT 0,
            total_rows      INTEGER NOT NULL DEFAULT 0,
            completed_rows  INTEGER NOT NULL DEFAULT 0,
            failed_rows     INTEGER NOT NULL DEFAULT 0,
            created_by      INTEGER,                 -- admin_users.id
            note            TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at      TIMESTAMP,
            completed_at    TIMESTAMP
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS backfill_job_rows (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id              INTEGER NOT NULL,
            position            INTEGER NOT NULL,    -- order in the input list
            title               TEXT NOT NULL,
            artist              TEXT NOT NULL,
            -- Compass-target metadata (NULL when target is library)
            year                INTEGER,
            chart_position      INTEGER,
            -- Library-target metadata (NULL when target is compass)
            album_id            INTEGER,
            track_number        INTEGER,
            -- Lyrics: paste-only in v1; Musixmatch fetch lands here too
            lyrics              TEXT,
            lyrics_source       TEXT,                -- 'paste' | 'musixmatch'
            -- Per-row state machine
            status              TEXT NOT NULL DEFAULT 'queued',
                                                    -- queued | needs_lyrics | calibrating
                                                    -- | tagging | done | failed | skipped
            error               TEXT,
            -- Result hand-off (which row was created/updated)
            result_song_source  TEXT,                -- 'compass' | 'library'
            result_song_id      INTEGER,
            -- Cached calibrator output keys for quick UI display
            rubric_color        TEXT,
            charge_value        INTEGER,
            deadpan_line        TEXT,
            topics              TEXT,                -- JSON array
            topic_audit         TEXT,                -- JSON object or NULL
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))

    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_backfill_job_rows_job_id "
        "ON backfill_job_rows(job_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_backfill_job_rows_status "
        "ON backfill_job_rows(job_id, status)"
    ))
