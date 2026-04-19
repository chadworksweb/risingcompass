"""Capture the song's charge_summary at the moment of recalibration.

The original 020 schema only snapshots before_charge + before_color, so a
recalibration that's later reverted can restore the tier but not the original
summary text. This adds before_summary to close that gap and make the audit
log fully reversible.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE song_recalibrations ADD COLUMN before_summary TEXT"
    ))
