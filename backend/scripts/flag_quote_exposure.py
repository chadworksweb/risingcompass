"""Existing-corpus quote-exposure triage (read-only, NO API calls).

The verbatim-lyric lock (lyric_quote_guard + rubric changes) protects everything
graded FROM NOW ON. This script measures the BACK-CATALOG -- rows graded under the
old rubric that may carry verbatim lyric quotes in publicly-displayed fields.

Two classes of exposure:
  1. DEFINITE by construction: every non-null contamination_note / dogma_note was
     written under a rubric that told the model to QUOTE the contaminating line /
     lyric moment. Treat all of them as needing a quote-free rewrite.
  2. PROBABLE: effects_prose / societal_effects_prose containing a double-quote
     character (the model wrapping an embedded lyric line in quotes).

Limitation (honest): we no longer store the original lyrics, so we cannot diff
prose against them. Verbatim lyric words woven in WITHOUT quotation marks are not
detectable here -- the going-forward lock + re-grade is what covers those. This
script gives a fast floor estimate of the cleanup workload.

Run (local must tunnel to the DB per repo CLAUDE.md):
    cd backend
    .venv\\Scripts\\python.exe scripts\\flag_quote_exposure.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal  # noqa: E402
from app.models import CompassSong, LibrarySong, SubmittedSong  # noqa: E402

DOUBLE_QUOTE_CHARS = ('"', "“", "”")  # straight + curly double quotes
PROSE_FIELDS = ("effects_prose", "societal_effects_prose")
NOTE_FIELDS = ("contamination_note", "dogma_note")  # old rubric instructed quoting
SAMPLE_LIMIT = 5


def _has_double_quote(val) -> bool:
    return bool(val) and any(c in val for c in DOUBLE_QUOTE_CHARS)


def _truncate(val: str, n: int = 140) -> str:
    val = " ".join(val.split())
    return val if len(val) <= n else val[:n] + " ..."


def audit_table(db, model, label: str) -> None:
    rows = db.query(model).all()
    total = len(rows)
    print(f"\n=== {label} (n={total}) ===")

    # Class 1: definite -- all non-null notes (old rubric told them to quote).
    for field in NOTE_FIELDS:
        if not hasattr(model, field):
            continue
        flagged = [r for r in rows if getattr(r, field, None)]
        print(f"  [DEFINITE] {field}: {len(flagged)} non-null (all need quote-free rewrite)")
        for r in flagged[:SAMPLE_LIMIT]:
            print(f"      #{r.id} {r.title!r}: {_truncate(getattr(r, field))}")

    # Class 2: probable -- prose containing a double-quote char.
    for field in PROSE_FIELDS:
        if not hasattr(model, field):
            continue
        flagged = [r for r in rows if _has_double_quote(getattr(r, field, None))]
        print(f"  [PROBABLE] {field}: {len(flagged)} contain a double-quote char")
        for r in flagged[:SAMPLE_LIMIT]:
            print(f"      #{r.id} {r.title!r}: {_truncate(getattr(r, field))}")


def main() -> None:
    db = SessionLocal()
    try:
        audit_table(db, CompassSong, "compass_songs (the Library + whitepaper corpus)")
        audit_table(db, LibrarySong, "library_songs")
        audit_table(db, SubmittedSong, "submitted_songs")
    finally:
        db.close()

    print(
        "\nNext: the DEFINITE rows (contamination_note / dogma_note) and the PROBABLE "
        "prose rows need a quote-free rewrite via Claude Code (no terminal API calls). "
        "Verbatim words woven in without quotes are not detectable here."
    )


if __name__ == "__main__":
    main()
