"""Audience Resonance -- store the coherence-check result on each resonance.

The coherence check is a fabrication SIGNAL (does the testimony track with the
song it is pinned to). Its result -- score + reasons -- is stored so the human
review queue can rank and explain why a row was routed for review. A low result
sets flag_state='in_review'; this column carries the why. Additive + idempotent.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE resonances ADD COLUMN IF NOT EXISTS coherence_json TEXT"
    ))
