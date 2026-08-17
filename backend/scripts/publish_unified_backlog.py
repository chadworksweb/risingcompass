"""Publish the historical Unified Charge Chart backlog. ONE-OFF, boundaried.

Decision 2026-08-16 (Chad): every historical day is published now, WITHOUT an
editorial. Future days keep the editorial gate exactly as built.

WHY THIS IS NOT A BACK DOOR

Publishing normally means supplying the editorial (POST /api/admin/unified/
editorial), because on this chart the prose is what RC says in the present tense
and it must be written against a figure that has stopped moving. That reasoning
governs TODAY and every day after it, and this script does not touch those.

The backlog is different in kind. Those 69 days were composed by
scripts/backfill_unified_readings.py from constituents that were themselves
approved long ago. They have settled: nothing left will recompose them, and their
degrees are arithmetic over per-chart readings that were already public on their
own chart pages. Demanding a retroactive editorial for each one would be ceremony
in front of a number the site already implies.

THE GUARD THAT KEEPS IT HONEST

`--through` is REQUIRED and has no default, so every run states its own boundary
out loud. Anything on or after that boundary is left alone, and the script
refuses a boundary at or past today unless --force is passed. That is what stops
this from quietly becoming "publish whatever has no editorial yet": a day that
misses its editorial today cannot be swept up by re-running this tomorrow without
someone deliberately widening the window and saying so.

WHAT IT DOES NOT DO

- Does not write, invent, or stub an editorial. Published-with-no-editorial is a
  real and legible state: chart-shell's editorialHtml returns empty on a falsy
  value, so those days simply render without an editorial line.
- Does not touch already-published days.
- Does not recompose. Numbers are whatever the composer last stored.
- Does not set editorial_stale. There is no prose for later numbers to invalidate.

Usage:
    cd backend
    .venv\\Scripts\\python.exe scripts\\publish_unified_backlog.py --through 2026-08-15
    .venv\\Scripts\\python.exe scripts\\publish_unified_backlog.py --through 2026-08-15 --apply
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import UnifiedReading  # noqa: E402
from app.services.charge_calc import degree_to_score  # noqa: E402


def _parse(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--through", type=_parse, required=True,
                    help="Publish unpublished readings up to and INCLUDING this "
                         "date (YYYY-MM-DD). Required; there is deliberately no "
                         "default.")
    ap.add_argument("--apply", action="store_true",
                    help="write (default is a dry run)")
    ap.add_argument("--force", action="store_true",
                    help="allow a boundary at or after today (normally refused)")
    args = ap.parse_args()

    today = date.today()
    if args.through >= today and not args.force:
        print(f"REFUSED: --through {args.through} is on or after today ({today}).\n"
              "Today and every day after it go through the editorial gate:\n"
              "  scripts/set_unified_editorial.py --editorial \"...\"\n"
              "Pass --force only if you mean to publish a current day with no prose.",
              file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        rows = (
            db.query(UnifiedReading)
            .filter(UnifiedReading.published.is_(False),
                    UnifiedReading.date <= args.through)
            .order_by(UnifiedReading.date)
            .all()
        )
        if not rows:
            print(f"Nothing to do: no unpublished readings on or before {args.through}.")
            return 0

        held = (
            db.query(UnifiedReading)
            .filter(UnifiedReading.published.is_(False),
                    UnifiedReading.date > args.through)
            .count()
        )

        print(f"{len(rows)} unpublished reading(s) on or before {args.through}"
              + ("" if args.apply else "   [DRY RUN, nothing will be written]"))
        print()
        for r in rows:
            print(f"  {r.date}  {degree_to_score(r.compass_degree):>+4} "
                  f"{r.charge_level:<7} {r.song_count:>3} songs  "
                  f"{r.source_count} chart(s)")

        if args.apply:
            stamp = datetime.now(timezone.utc)
            for r in rows:
                r.published = True
                r.published_at = stamp
                # editorial stays NULL and editorial_stale stays False on purpose.
            db.commit()

        print()
        print(f"{len(rows)} day(s) {'PUBLISHED' if args.apply else 'would publish'}, "
              "no editorial written.")
        print(f"{held} day(s) after {args.through} left alone for the editorial gate.")
        if not args.apply:
            print("\nDRY RUN: re-run with --apply to write.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
