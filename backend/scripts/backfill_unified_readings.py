"""Backfill the Unified Charge Chart series over a date range.

Going forward, every constituent approval recomposes its day (the hook in
agent.approve_draft). This builds the history that predates the hook.

COMPOSITION BANDING (read before trusting the series)

The constituents did not all start on the same day:

    spotify_top50_usa      daily readings from 2026-02-14
    itunes_download_usa    published snapshots from 2026-06-08
    shazam_top200_usa      from 2026-06-10
    youtube_trending_usa   from 2026-06-11

So a backfilled series changes INSTRUMENT partway through: two sources, then
three, then four. Comparing a 4-source day against a 2-source day is the same
cross-depth comparability problem RISING-COMPASS-CHARGE-WEIGHTING.md section 1
dealt with inside a single chart, and it has to be visible rather than smoothed
over. Every stored row carries `sources_included` and `source_count`, so the
banding is queryable; the report below prints where the composition changes so
the boundaries are known before anything is published.

A day composed from ONE constituent is never stored (unified_store.MIN_SOURCES).
A "unified" chart of one chart is that chart under another name, so the series
simply begins where the second source does.

DOES NOT PUBLISH. Every backfilled row lands unpublished, because the editorial
is the publication gate (scope 8.6) and no editorial exists for a historical day.
The rows are there for the series, the Calendar, and analysis; making any of them
public is a separate, deliberate act.

IDEMPOTENT: recompose upserts per date, so re-running is safe and picks up
anything that has since been corrected. Re-running never unpublishes and never
overwrites an editorial.

Usage:
    cd backend
    .venv\\Scripts\\python.exe scripts\\backfill_unified_readings.py
    .venv\\Scripts\\python.exe scripts\\backfill_unified_readings.py --apply
    .venv\\Scripts\\python.exe scripts\\backfill_unified_readings.py --apply --from 2026-06-11
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.services import unified_store  # noqa: E402
from app.services.charge_calc import degree_to_score  # noqa: E402
from app.services.unified_chart import compose  # noqa: E402


def _parse(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the readings (default is a dry run)")
    ap.add_argument("--from", dest="start", type=_parse, default=None,
                    help="start date (default: earliest day with 2+ constituents)")
    ap.add_argument("--to", dest="end", type=_parse, default=None,
                    help="end date (default: today)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.start is None:
            # Earliest date any snapshot constituent published. Before that only
            # the daily reading exists, which is one source and never stored.
            row = db.execute(text("""
                SELECT MIN(date) FROM chart_snapshots
                WHERE published IS TRUE AND chart_source IN (
                    'itunes_download_usa','shazam_top200_usa','youtube_trending_usa')
            """)).scalar()
            if row is None:
                print("No published constituent snapshots. Nothing to backfill.")
                return 0
            args.start = row
        if args.end is None:
            args.end = db.execute(text("SELECT MAX(date) FROM daily_readings")).scalar() \
                or date.today()

        weights = unified_store.load_weights(db)
        print(f"range {args.start} .. {args.end}"
              + ("" if args.apply else "   [DRY RUN, nothing will be written]"))
        print(f"weights {weights} (version {unified_store.weights_version(weights)})")
        print()

        stored = skipped = 0
        band_counts: Counter = Counter()
        prev_band = None
        rows_out = []

        day = args.start
        while day <= args.end:
            composed = compose(db, day, weights=weights)
            n = 0 if composed is None else len(composed.sources_included)

            if composed is None or n < unified_store.MIN_SOURCES:
                skipped += 1
                rows_out.append((day, n, None, None, None))
            else:
                band = tuple(sorted(c.slug for c in composed.sources_included))
                band_counts[band] += 1
                if band != prev_band:
                    rows_out.append(("BAND", band, None, None, None))
                    prev_band = band
                rows_out.append((day, n, composed.compass_degree,
                                 composed.charge_level, composed.song_count))
                if args.apply:
                    unified_store.store(db, composed, commit=False)
                stored += 1
            day += timedelta(days=1)

        if args.apply:
            db.commit()

        for r in rows_out:
            if r[0] == "BAND":
                names = ", ".join(s.split("_")[0] for s in r[1])
                print(f"\n--- composition changes to {len(r[1])} sources: {names} ---")
                continue
            d, n, deg, lvl, songs = r
            if deg is None:
                print(f"{str(d):12} {n} source(s)   skipped (below MIN_SOURCES)")
            else:
                print(f"{str(d):12} {n} sources  {degree_to_score(deg):>+5}  "
                      f"{lvl:<7} {songs:>3} songs")

        print()
        print(f"{stored} day(s) composed, {skipped} skipped")
        print("composition bands:")
        for band, cnt in band_counts.most_common():
            print(f"  {len(band)} sources ({', '.join(s.split('_')[0] for s in band)}): "
                  f"{cnt} day(s)")
        print()
        print("All rows land UNPUBLISHED. The editorial is the publication gate; "
              "no historical day has one.")
        if not args.apply:
            print("DRY RUN: re-run with --apply to write.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
