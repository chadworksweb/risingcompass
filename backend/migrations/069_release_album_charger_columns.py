"""Album-level synthesis columns on releases (Album Charger).

The Album Charger is the first public path that creates a Release directly
from user-supplied metadata. Beyond the aggregate fields a Release already
carries (charge_value, rubric_color, track_count, calibrated_count,
contamination_count), an album-level reading needs its own synthesized prose:

  charge_summary  -- one-paragraph album-level summary
  arc_prose       -- how the album moves across its tracks
  societal_prose  -- what running this album at scale does to a society
  source          -- provenance tag ('album_charger' for user-charged albums;
                     NULL for MusicBrainz/Spotify-derived releases)
  submitted_at    -- when the album was charged via the Album Charger

PG-compatible (063+). Mirrors the per-song prose columns on submitted_songs.
"""

from sqlalchemy import text


def up(conn):
    for column, coltype in (
        ("charge_summary", "TEXT"),
        ("arc_prose", "TEXT"),
        ("societal_prose", "TEXT"),
        ("source", "VARCHAR(30)"),
        ("submitted_at", "TIMESTAMP"),
    ):
        conn.execute(text(
            f"ALTER TABLE releases ADD COLUMN IF NOT EXISTS {column} {coltype}"
        ))
