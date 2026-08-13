"""Rebuild an artist's release catalogue under the codified filter.

Wraps the admin rebuild-releases action (RISING-COMPASS-ARTIST-RELEASES.md) with
the retry loop and the post-rebuild junk scan that the SOP asks for, so a
catalogue repair is one command instead of an ad-hoc script each time.

WHY THIS EXISTS: resolve-metadata is ADDITIVE and skips by MBID, so it can never
retroactively apply a tightened filter. Only rebuild-releases can. Between
2026-06-06 and 2026-08-13 the filter was codified but never applied to anyone,
and every catalogue on the site was still the ad-hoc filter's output.

SAFETY: rebuild-releases fetches from MusicBrainz BEFORE it purges, so an outage
aborts with the artist's existing data untouched. Every failure path here is a
no-op on the data. MusicBrainz 503s freely under load and a retry is normal, not
a fault.

Usage:
    cd backend
    .venv\\Scripts\\python.exe scripts\\rebuild_artist_catalog.py --slug the-beatles
    .venv\\Scripts\\python.exe scripts\\rebuild_artist_catalog.py --stale
    .venv\\Scripts\\python.exe scripts\\rebuild_artist_catalog.py --slug bts --scan-only

Full procedure: RISING-COMPASS-ARTIST-CATALOG-SOP.md
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402
from app.routers.artists_admin import (  # noqa: E402
    RebuildReleasesRequest, rebuild_releases,
)

logging.basicConfig(level=logging.ERROR, stream=sys.stdout,
                    format="%(asctime)s %(levelname)s: %(message)s")

# The codified filter was committed on this date. Anything resolved earlier is
# the ad-hoc filter's output and has never had the current rule applied.
FILTER_CODIFIED = "2026-06-06"

# Titles that smell like something the filter should have caught. Advisory only:
# this prints candidates for a human, it never deletes. Real removals go through
# the suppression endpoint so they survive the next rebuild.
SUSPECT = re.compile(
    r"karaoke|tribute|in the style of|originally performed|made popular|"
    r"interview|commentary|soundboard|unauthoriz|bootleg|"
    r"a ?cappella|instrumental version",
    re.I,
)


def _q(sql: str, **params):
    with engine.connect() as c:
        return list(c.execute(text(sql), params))


def stale_slugs() -> list[tuple[str, str, int]]:
    """Artists whose releases predate the codified filter."""
    rows = _q(
        """
        with per as (
          select artist_id, count(*) n, max(created_at) mx
            from releases where musicbrainz_id is not null group by artist_id)
        select a.slug, a.name, per.n
          from per join artists a on a.id = per.artist_id
         where per.mx < cast(:cutoff as timestamp)
         order by per.n desc
        """,
        cutoff=FILTER_CODIFIED,
    )
    return [(r.slug, r.name, r.n) for r in rows]


def release_count(slug: str) -> int:
    rows = _q(
        "select count(*) n from releases r join artists a on a.id = r.artist_id"
        " where a.slug = :s",
        s=slug,
    )
    return rows[0].n if rows else 0


def scan(slug: str) -> None:
    """Flag likely-junk titles and report anything stranded in the catch-all."""
    rows = _q(
        "select r.title, r.release_type, r.release_year from releases r"
        " join artists a on a.id = r.artist_id"
        " where a.slug = :s and r.musicbrainz_id is not null"
        " order by r.release_year nulls last",
        s=slug,
    )
    flagged = [r for r in rows if SUSPECT.search(r.title)]
    print(f"  scanned {len(rows)} releases; {len(flagged)} flagged for review")
    for r in flagged:
        print(f"    [{r.release_year or '?'}] {r.release_type:<6} {r.title}")

    # A catch-all carries no release_date, and the trajectory filters on
    # release_date IS NOT NULL -- so every song parked here is invisible to the
    # artist's arc. Worth surfacing even though it is not a junk problem.
    cat = _q(
        "select r.track_count, r.calibrated_count from releases r"
        " join artists a on a.id = r.artist_id"
        " where a.slug = :s and r.title = 'Singles & Uncategorized'",
        s=slug,
    )
    if cat and (cat[0].calibrated_count or 0):
        print(f"    NOTE: {cat[0].calibrated_count} calibrated song(s) sit in the"
              f" catch-all and are INVISIBLE to the trajectory (no release_date)")


async def rebuild_one(slug: str, name: str, attempts: int) -> dict:
    before = release_count(slug)
    print(f"=== {name} ({slug}) -- {before} releases ===", flush=True)
    req = RebuildReleasesRequest(
        notes="Applying the codified first-appearance filter "
              "(RISING-COMPASS-ARTIST-RELEASES.md)."
    )
    for attempt in range(1, attempts + 1):
        try:
            res = await rebuild_releases(slug, req)
        except HTTPException as e:
            # 503 means MusicBrainz, and the artist's data was NOT touched.
            print(f"  attempt {attempt} aborted {e.status_code} (data untouched)",
                  flush=True)
            if attempt < attempts:
                wait = attempt * 45
                print(f"  retrying in {wait}s", flush=True)
                await asyncio.sleep(wait)
            continue

        after = release_count(slug)
        print(f"  {before} -> {after} ({after - before:+d})  "
              f"created={res.get('releases_created')} "
              f"suppressed={res.get('releases_suppressed', 0)} "
              f"links={res.get('songs_linked')}", flush=True)
        scan(slug)
        print(flush=True)
        return {"slug": slug, "name": name, "before": before, "after": after,
                "status": "ok"}

    print("  SKIPPED: MusicBrainz would not answer. Data untouched.\n", flush=True)
    return {"slug": slug, "name": name, "before": before, "after": before,
            "status": "skipped"}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--slug", help="rebuild one artist by slug")
    g.add_argument("--stale", action="store_true",
                   help="rebuild every artist whose releases predate the filter")
    ap.add_argument("--attempts", type=int, default=3,
                    help="MusicBrainz attempts per artist (default 3)")
    ap.add_argument("--scan-only", action="store_true",
                    help="report junk candidates without rebuilding")
    args = ap.parse_args()

    if args.slug:
        db = SessionLocal()
        try:
            row = db.execute(
                text("select name from artists where slug = :s"), {"s": args.slug}
            ).first()
        finally:
            db.close()
        if not row:
            print(f"No artist with slug {args.slug!r}")
            return 1
        targets = [(args.slug, row.name, release_count(args.slug))]
    else:
        targets = stale_slugs()
        if not targets:
            print(f"No artist has releases predating {FILTER_CODIFIED}. "
                  "Every catalogue is current.")
            return 0
        print(f"{len(targets)} artist(s) with pre-filter releases\n")

    if args.scan_only:
        for slug, name, n in targets:
            print(f"=== {name} ({slug}) -- {n} releases ===")
            scan(slug)
            print()
        return 0

    results = []
    for slug, name, _ in targets:
        results.append(await rebuild_one(slug, name, args.attempts))

    print("===== SUMMARY =====")
    for r in results:
        if r["status"] == "ok":
            print(f"  {r['name'][:28]:<28} {r['before']:>4} -> {r['after']:<4} "
                  f"({r['after'] - r['before']:+d})")
        else:
            print(f"  {r['name'][:28]:<28} {r['before']:>4} -> unchanged  [skipped]")
    skipped = [r for r in results if r["status"] != "ok"]
    if skipped:
        print(f"\n{len(skipped)} skipped. Re-run later; nothing was lost.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
