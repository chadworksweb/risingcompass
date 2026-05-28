"""One-time audit: where did calibrated lyrics come from?

Reports BackfillJobRow.lyrics_source counts. Musixmatch-sourced rows would be a
ToS 2.2.14 exposure (their data fed to an AI). Expected result for Rising Compass:
only 'paste' / NULL, because the Musixmatch fetch path requires a configured key
that was never set. This script confirms it.

Note: the canonical compass_songs table has no lyrics_source column, so the only
per-row provenance record is BackfillJobRow.lyrics_source (the staging table that
fed the backfill). That is what this audits.

Run (local must tunnel to the DB per repo CLAUDE.md):
    cd backend
    .venv\\Scripts\\python.exe scripts\\audit_lyrics_source.py
"""

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal  # noqa: E402
from app.models import BackfillJobRow  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        rows = db.query(BackfillJobRow.lyrics_source).all()
    finally:
        db.close()

    by_source = Counter((src or "NULL") for (src,) in rows)
    print(f"Total backfill_job_rows: {len(rows)}")
    for src, n in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f"  lyrics_source={src}: {n}")

    mm = by_source.get("musixmatch", 0)
    if mm:
        print(
            f"\nWARNING: {mm} row(s) have lyrics_source='musixmatch' -- ToS 2.2.14 "
            "exposure. These calibrations were built by prompting the AI with "
            "Musixmatch lyrics. Flag for legal review."
        )
    else:
        print(
            "\nCLEAN: no Musixmatch-sourced rows. Backfill corpus was AI-calibrated "
            "from pasted / manually-supplied lyrics only."
        )


if __name__ == "__main__":
    main()
