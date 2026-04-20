"""Calibration runs — append-only log of every agent run on a song.

Every time the classifier runs — LC submit, compass daily agent, stream
calibration, admin library edits — we log the full output here. Over time
this builds:

  1. A self-reinforcing consensus per song (mean of runs, weighted by
     confidence) that the canonical song row tracks as new runs come in.
  2. A training corpus: (song_identity, agent_output, timestamp, model).
     Lyrics hash only — lyrics themselves are never stored, per LC's promise.

Polymorphic song pointer (song_source, song_id) is nullable because a first-
time calibration via LC may produce a row here before the submitted_song
row commits (rare) or when no persisted song exists. title + artist always
snapshot.

Immutable. No updates — drift is a new row.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS calibration_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_source VARCHAR(20),
            song_id INTEGER,
            title TEXT,
            artist TEXT,
            rubric_color VARCHAR(20),
            charge_value INTEGER,
            charge_summary TEXT,
            contaminated BOOLEAN DEFAULT 0,
            contamination_note TEXT,
            confidence REAL,
            agent_model VARCHAR(80),
            triggered_by VARCHAR(40),
            lyrics_hash VARCHAR(64),
            run_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_calibration_runs_song "
        "ON calibration_runs(song_source, song_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_calibration_runs_title_artist "
        "ON calibration_runs(title, artist)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_calibration_runs_run_at "
        "ON calibration_runs(run_at DESC)"
    ))
