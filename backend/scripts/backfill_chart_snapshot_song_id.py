"""Backfill chart_snapshots.song_id on rows that predate migration 154.

Approval stamps song_id going forward (agent.approve_draft, chart branch), but
every snapshot row published before that lands carries NULL, and the draft that
knew the answer was deleted by _cleanup_day_drafts the moment it was approved.
So the historical rows have to be re-resolved from their stored strings.

This runs the SAME ladder every write chokepoint uses,
`song_identity.resolve_song_identity`, so a row resolved here is resolved by
exactly the rule that would have applied at approval time. That matters more than
convenience: a bespoke matcher would drift from the live one and the historical
half of any cross-chart series would quietly obey different rules from the recent
half. The ladder also handles precisely the feeder drift that makes plain string
matching lose 8 to 14 percent of rows (VEVO and Topic artist suffixes, the
`ARTIST - TITLE (Official Video)` form, `| @channel` tails), via
feeder_clean.clean_title_artist on rung 2.

Deliberately does NOT mint songs. `resolve_song_identity` returning `new` means
no Library row exists for that string, and inventing one from a chart slot would
create an uncalibrated song with no lyrics, no reading, and no ingestion record.
Those rows stay NULL, which is the honest answer: no confirmed identity. Anything
unioning across charts treats NULL as ineligible.

Also does NOT act on the trgm gray band. When the ladder returns candidates
instead of a confident match, that is a merge-queue question for a human
(`song_merge_candidates`), not something a backfill should decide unattended.
Counted and reported, never written.

IDEMPOTENT: only touches rows WHERE song_id IS NULL, so re-running picks up
whatever the last pass could not resolve plus anything new. Safe to re-run after
a merge pass or after the trgm flag is flipped, both of which can turn a previous
miss into a hit.

READ-ONLY BY DEFAULT. Prints what it would do and changes nothing until --apply
is passed, because this writes to a table that feeds public chart pages.

Usage:
    cd backend
    .venv\\Scripts\\python.exe scripts\\backfill_chart_snapshot_song_id.py
    .venv\\Scripts\\python.exe scripts\\backfill_chart_snapshot_song_id.py --apply
    .venv\\Scripts\\python.exe scripts\\backfill_chart_snapshot_song_id.py --apply --chart shazam_top200_usa
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.services.song_identity import resolve_song_identity  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the resolved ids (default is a dry run)")
    ap.add_argument("--chart", default=None,
                    help="limit to one chart_source slug")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N rows (0 = all)")
    ap.add_argument("--published-only", action="store_true",
                    help="skip unpublished fetch-time rows")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        where = ["song_id IS NULL"]
        params: dict = {}
        if args.chart:
            where.append("chart_source = :chart")
            params["chart"] = args.chart
        if args.published_only:
            where.append("published IS TRUE")
        sql = (
            "SELECT id, date, chart_source, position, title, artist "
            "FROM chart_snapshots WHERE " + " AND ".join(where) +
            " ORDER BY date DESC, chart_source, position"
        )
        if args.limit:
            sql += f" LIMIT {int(args.limit)}"

        rows = db.execute(text(sql), params).fetchall()
        if not rows:
            print("Nothing to do: no chart_snapshots rows with a NULL song_id.")
            return 0

        print(f"{len(rows)} row(s) with NULL song_id"
              + (f" on {args.chart}" if args.chart else "")
              + ("" if args.apply else "   [DRY RUN, nothing will be written]"))

        via_counts: Counter = Counter()
        per_chart: Counter = Counter()
        per_chart_hit: Counter = Counter()
        gray_band = []
        resolved = 0

        for row in rows:
            per_chart[row.chart_source] += 1
            res = resolve_song_identity(db, row.title, row.artist)
            via_counts[res.via] += 1

            if res.song_id is None:
                if res.candidates:
                    # Gray band: the fuzzy rung found plausible matches but not a
                    # confident one. That is a human merge-queue decision.
                    gray_band.append((row.id, row.title, row.artist, res.candidates))
                continue

            resolved += 1
            per_chart_hit[row.chart_source] += 1
            if args.apply:
                db.execute(
                    text("UPDATE chart_snapshots SET song_id = :sid WHERE id = :rid"),
                    {"sid": res.song_id, "rid": row.id},
                )

        if args.apply:
            db.commit()

        print()
        print(f"resolved {resolved} / {len(rows)} "
              f"({resolved * 100 // max(1, len(rows))}%)")
        print()
        print("by rung:")
        for via, n in via_counts.most_common():
            print(f"  {via:<14} {n}")
        print()
        print("by chart:")
        for slug, n in per_chart.most_common():
            hit = per_chart_hit.get(slug, 0)
            print(f"  {slug:<24} {hit} / {n}  ({hit * 100 // max(1, n)}%)")

        if gray_band:
            print()
            print(f"{len(gray_band)} row(s) in the trgm gray band, NOT written "
                  "(a human merge-queue decision, not a backfill's):")
            for rid, title, artist, cands in gray_band[:15]:
                print(f"  snapshot {rid}: {title} / {artist} -> candidates {cands}")
            if len(gray_band) > 15:
                print(f"  ... and {len(gray_band) - 15} more")

        unresolved = len(rows) - resolved
        if unresolved:
            print()
            print(f"{unresolved} row(s) left NULL. That is the correct outcome "
                  "for a chart slot with no Library song: this script never "
                  "mints one, and NULL reads as 'no confirmed identity'.")

        if not args.apply:
            print()
            print("DRY RUN: re-run with --apply to write.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
