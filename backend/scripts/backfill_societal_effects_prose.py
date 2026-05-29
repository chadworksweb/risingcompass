"""One-shot: populate societal_effects_prose for every calibrated song missing it.

Iterates compass_songs + library_songs + submitted_songs + stream_songs.
Requires the song to already have:
  - charge_value + rubric_color + charge_summary (calibration done)
  - effects_prose (per-listener prose run; we reference it for context)

Songs without ether tags (deadpan_line + topics) still get processed — the
prompt degrades gracefully — but the read is sharper when topics are present,
so prefer running this AFTER the ether tagger has caught up.

Direct libsql connection against Turso primary, same pattern as
backfill_effects_prose.py. Idempotent; re-running only touches NULL rows.

Ordering: --top-first sorts compass_songs by chart_position ASC so the daily
top 20 (and top 100) get covered first. Other tables fall through in id order.

Usage:
    cd backend
    .venv/Scripts/python.exe scripts/backfill_societal_effects_prose.py --top-first
    .venv/Scripts/python.exe scripts/backfill_societal_effects_prose.py --limit 20 --source compass --top-first
    .venv/Scripts/python.exe scripts/backfill_societal_effects_prose.py --source library
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

import libsql

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from app.services.societal_effects_prose import generate_societal_effects_prose  # noqa: E402


TABLES = [
    ("compass", "compass_songs"),
    ("library", "library_songs"),
    ("submitted", "submitted_songs"),
    ("stream", "stream_songs"),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None,
                   help="Max rows to process across all tables")
    p.add_argument("--source", choices=[s for s, _ in TABLES],
                   help="Limit to a single source table")
    p.add_argument("--top-first", action="store_true",
                   help="On compass_songs, order by chart_position ASC (top 20 first)")
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
        order_clause = "ORDER BY id"
        if args.top_first and table == "compass_songs":
            # NULLS LAST so songs without a position drop to the end
            order_clause = "ORDER BY (chart_position IS NULL), chart_position ASC, id"

        rows = conn.execute(
            f"SELECT id, title, artist, rubric_color, charge_value, "
            f"       charge_summary, contaminated, contamination_note, "
            f"       deadpan_line, topics, effects_prose "
            f"FROM {table} "
            f"WHERE charge_value IS NOT NULL "
            f"  AND rubric_color IS NOT NULL "
            f"  AND charge_summary IS NOT NULL "
            f"  AND (societal_effects_prose IS NULL OR societal_effects_prose = '') "
            f"{order_clause}"
        ).fetchall()

        if not rows:
            print(f"[{source}] nothing to do")
            continue

        print(f"[{source}] {len(rows)} rows to process")

        for (sid, title, artist, color, charge, summary,
             contaminated, contam_note, deadpan, topics, eff_prose) in rows:
            if args.limit and total_processed >= args.limit:
                print(f"--limit {args.limit} reached; stopping")
                break

            total_processed += 1
            try:
                result = generate_societal_effects_prose(
                    title=title or "",
                    artist=artist or "",
                    rubric_color=color,
                    charge_value=charge,
                    charge_summary=summary,
                    contaminated=bool(contaminated),
                    contamination_note=contam_note,
                    deadpan_line=deadpan,
                    topics=topics,
                    effects_prose=eff_prose,
                )
            except Exception as e:
                total_failed += 1
                print(f"  [{source}/{sid}] FAILED: {e}")
                continue

            if not result:
                total_failed += 1
                print(f"  [{source}/{sid}] no prose returned: {title} by {artist}")
                continue

            # Write prose + sealed provenance in lockstep. generated_at is
            # serialised to ISO text for the SQLite/libsql columns.
            conn.execute(
                f"UPDATE {table} SET societal_effects_prose = ?, "
                f"societal_prose_generated_at = ?, societal_prose_model = ? "
                f"WHERE id = ?",
                (result.prose, result.generated_at.isoformat(sep=" "),
                 result.model, sid),
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
