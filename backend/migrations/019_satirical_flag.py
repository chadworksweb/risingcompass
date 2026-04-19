"""Extend misread_submissions for the Satirical Flag and polymorphic song keys.

Adds:
  - report_type ('misread' | 'satirical') — what kind of flag this is.
  - proof_context — required when report_type='satirical'; the case the user makes.
  - song_source / song_id — polymorphic ref into compass_songs / library_songs /
    submitted_songs. Nullable; the original (song_title, song_artist) strings
    remain as the canonical record of what the user typed and as fallback when
    the slug matcher can't resolve a song.

Indexes support:
  - filtering the admin queue by report_type
  - public flag-count lookups by polymorphic key
  - public flag-count fallback by (song_title, song_artist)
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE misread_submissions "
        "ADD COLUMN report_type VARCHAR(20) NOT NULL DEFAULT 'misread'"
    ))
    conn.execute(text("ALTER TABLE misread_submissions ADD COLUMN proof_context TEXT"))
    conn.execute(text("ALTER TABLE misread_submissions ADD COLUMN song_source VARCHAR(20)"))
    conn.execute(text("ALTER TABLE misread_submissions ADD COLUMN song_id INTEGER"))

    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_misread_report_type "
        "ON misread_submissions(report_type)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_misread_song_poly "
        "ON misread_submissions(song_source, song_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_misread_song_strings "
        "ON misread_submissions(song_title, song_artist)"
    ))
