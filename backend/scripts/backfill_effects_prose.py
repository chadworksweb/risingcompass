"""One-shot: populate effects_prose for every calibrated song missing it.

Iterates compass_songs + library_songs + submitted_songs + cl_stream_songs.
For each row with charge_value IS NOT NULL AND effects_prose IS NULL,
calls the effects-prose agent and writes the result.

Direct libsql connection against Turso primary — same pattern as
backfill_song_slugs.py. Idempotent; re-running only touches NULL rows.

Budget: ~0.8s per Claude call * ~500 songs = ~7 min serially. Run at
your leisure; the column is always optional and the site falls back to
tier-generic copy when it's NULL.

Usage:
    cd backend
    .venv/Scripts/python.exe scripts/backfill_effects_prose.py
    .venv/Scripts/python.exe scripts/backfill_effects_prose.py --limit 50
    .venv/Scripts/python.exe scripts/backfill_effects_prose.py --source compass
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

import libsql

# The prose generator reads settings via app.config; make app importable.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from app.services.effects_prose import generate_effects_prose  # noqa: E402


TABLES = [
    ("compass", "compass_songs"),
    ("library", "library_songs"),
    ("submitted", "submitted_songs"),
    ("stream", "cl_stream_songs"),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None,
                   help="Max rows to process across all tables")
    p.add_argument("--source", choices=[s for s, _ in TABLES],
                   help="Limit to a single source table")
    p.add_argument("--sleep", type=float, default=0.1,
                   help="Seconds to sleep between calls (default 0.1)")
    args = p.parse_args()

    url = os.environ["DATABASE_URL"]
    token = os.environ["TURSO_AUTH_TOKEN"]
    conn = libsql.connect(database=url, auth_token=token)

    total_processed = 0
    total_generated = 0
    total_failed = 0

    tables_to_process = (
        [(args.source, dict(TABLES)[args.source])]
        if args.source else TABLES
    )

    for source, table in tables_to_process:
        rows = conn.execute(
            f"SELECT id, title, artist, rubric_color, charge_value, "
            f"       charge_summary, contaminated, contamination_note "
            f"FROM {table} "
            f"WHERE charge_value IS NOT NULL "
            f"  AND rubric_color IS NOT NULL "
            f"  AND charge_summary IS NOT NULL "
            f"  AND (effects_prose IS NULL OR effects_prose = '') "
            f"ORDER BY id"
        ).fetchall()

        if not rows:
            print(f"[{source}] nothing to do")
            continue

        print(f"[{source}] {len(rows)} rows to process")

        for (sid, title, artist, color, charge, summary,
             contaminated, contam_note) in rows:
            if args.limit and total_processed >= args.limit:
                print(f"--limit {args.limit} reached; stopping")
                break

            total_processed += 1
            try:
                prose: Optional[str] = generate_effects_prose(
                    title=title or "",
                    artist=artist or "",
                    rubric_color=color,
                    charge_value=charge,
                    charge_summary=summary,
                    contaminated=bool(contaminated),
                    contamination_note=contam_note,
                )
            except Exception as e:
                total_failed += 1
                print(f"  [{source}/{sid}] FAILED: {e}")
                continue

            if not prose:
                total_failed += 1
                print(f"  [{source}/{sid}] no prose returned: {title} by {artist}")
                continue

            conn.execute(
                f"UPDATE {table} SET effects_prose = ? WHERE id = ?",
                (prose, sid),
            )
            conn.commit()
            total_generated += 1
            print(f"  [{source}/{sid}] OK: {title} by {artist}")
            time.sleep(args.sleep)

        if args.limit and total_processed >= args.limit:
            break

    print(f"\nTOTAL processed: {total_processed}")
    print(f"  generated: {total_generated}")
    print(f"  failed:    {total_failed}")
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
