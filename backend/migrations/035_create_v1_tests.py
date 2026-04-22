"""v1_tests — isolated write target for v1-frozen control calibrations.

Runs against the same Turso DB as everything else but on its own table
so results don't bleed into compass_songs / submitted_songs / the
corpus. Powers the admin "V1 Test" tab: admin pastes lyrics, the v1
calibrator (pinned to commit a839df9) scores them, one row lands here.

The row captures rubric_commit so if we later snapshot v1.1, runs
stay distinguishable.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS v1_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            rubric_color TEXT,
            charge_value INTEGER,
            contaminated BOOLEAN DEFAULT 0,
            contamination_note TEXT,
            charge_summary TEXT,
            confidence REAL,
            rubric_commit TEXT NOT NULL,
            error TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_v1_tests_created_at "
        "ON v1_tests(created_at DESC)"
    ))
