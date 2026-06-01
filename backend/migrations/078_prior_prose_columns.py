"""Add prior-prose backup columns to all four prose-bearing song tables.

When the dedicated prose-regeneration path rewrites effects_prose /
societal_effects_prose it first archives the previous values here so the
old text (and its provenance seal) remains accessible without relying on
provenance_anchor records or git blame.

Columns added to compass_songs, library_songs, submitted_songs, stream_songs:
  prior_effects_prose              TEXT NULL
  prior_societal_effects_prose     TEXT NULL
  prior_societal_prose_generated_at  TIMESTAMP NULL
  prior_societal_prose_model       TEXT NULL

PG-compatible (063+). Idempotent via IF NOT EXISTS.
"""

from sqlalchemy import text

_TABLES = [
    "compass_songs",
    "library_songs",
    "submitted_songs",
    "stream_songs",
]

_COLUMNS = [
    ("prior_effects_prose", "TEXT"),
    ("prior_societal_effects_prose", "TEXT"),
    ("prior_societal_prose_generated_at", "TIMESTAMP"),
    ("prior_societal_prose_model", "TEXT"),
]


def up(conn):
    for table in _TABLES:
        for col, typ in _COLUMNS:
            conn.execute(text(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typ}"
            ))
