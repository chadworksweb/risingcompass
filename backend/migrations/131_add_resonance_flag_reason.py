"""Audience Resonance -- persist the flag reason ("did we misread your story").

The submission wizard and the per-resonance flag both collect a short note about
what the slicer got wrong; that note is the training signal for the resonance
rubric, so it must be stored, not dropped. Adds a nullable TEXT column to
`resonances`. Additive + idempotent (ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE resonances ADD COLUMN IF NOT EXISTS flag_reason TEXT"
    ))
