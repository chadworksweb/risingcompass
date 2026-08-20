"""Attach release-group MBIDs to releases that have none -- which is what gives
them cover art.

The manual twin of the nightly sweep (`POST /api/admin/agent/cron/cover-art`).
Both call `services/release_mbid.py`, so running this by hand and letting the
cron run cannot drift apart.

Releases carrying a reading are resolved FIRST: those are the pages someone is
meant to look at. Every match must clear the Album Charger's confidence rule --
a high score, an exact title-slug match, and a clear margin over the runner-up --
because no art beats wrong art.

Usage:
    cd backend
    .venv\\Scripts\\python.exe scripts\\backfill_release_mbid.py [--limit N] [--dry-run]
    .venv\\Scripts\\python.exe scripts\\backfill_release_mbid.py --release-id 1932
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal          # noqa: E402
from app.models import Release                 # noqa: E402
from app.services import musicbrainz, release_mbid  # noqa: E402


async def _preview(release_id: int) -> None:
    """Show what a resolve WOULD pick, without writing anything."""
    db = SessionLocal()
    try:
        rel = db.get(Release, release_id)
        if rel is None:
            print(f"  {release_id}: no such release")
            return
        if rel.musicbrainz_id:
            print(f"  {release_id}: already set -> {rel.musicbrainz_id}")
            return
        from sqlalchemy import text
        artist = db.execute(text("SELECT name FROM artists WHERE id = :a"),
                            {"a": rel.artist_id}).scalar()
        title = rel.title
        hint = release_mbid._track_mbid_hint(db, release_id)
    finally:
        db.close()

    cands = await musicbrainz.search_release_group(artist or "", title or "")
    chosen, needs_pick, top = release_mbid.pick_mb_match(title or "", cands)
    if not chosen and hint and any(c["mbid"] == hint for c in cands):
        chosen, needs_pick = hint, False
    verdict = chosen or ("AMBIGUOUS" if needs_pick else "no match")
    print(f"  {release_id}: {title} - {artist} -> {verdict}")
    for c in top[:3]:
        print(f"      {c['score']:>3} {c['primary_type'] or 'release':11} "
              f"{c['first_release_date'] or '':10} {c['title']}")


async def main() -> None:
    p = argparse.ArgumentParser(description="Attach release-group MBIDs + cover art.")
    p.add_argument("--limit", type=int, default=25, help="Cap releases resolved this run.")
    p.add_argument("--release-id", type=int, default=None, help="Resolve one release.")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be picked; write nothing.")
    p.add_argument("--recheck-misses", action="store_true",
                   help="Revisit releases recorded as a definitive miss "
                        "(mbid_checked_at set). Mirrors the song lane's flag.")
    args = p.parse_args()

    # --release-id bypasses the pending filters on purpose: naming a release
    # explicitly is a deliberate act, and it must work on a recorded miss and on
    # the catch-all bucket without needing a second flag.
    ids = ([args.release_id] if args.release_id
           else release_mbid.pending_release_ids(
               limit=args.limit, recheck_misses=args.recheck_misses))
    if not ids:
        print("Release MBIDs up to date -- nothing without one.")
        return

    if args.dry_run:
        print(f"DRY RUN over {len(ids)} release(s) (~1s each against MusicBrainz):")
        for rid in ids:
            await _preview(rid)
        return

    print(f"Resolving {len(ids)} release(s) against MusicBrainz "
          f"(~2s each incl. the CAA check, est. {len(ids) * 2}s)...")
    counts = {"attached": 0, "already_set": 0, "ambiguous": 0, "not_found": 0,
              "missing": 0, "with_art": 0}
    for i, rid in enumerate(ids, 1):
        res = await release_mbid.resolve_release_mbid(rid)
        counts[res["status"]] = counts.get(res["status"], 0) + 1
        if res["status"] == "attached" and res.get("has_art"):
            counts["with_art"] += 1
        art = "" if res["status"] != "attached" else f" art={res['has_art']}"
        print(f"  [{i}/{len(ids)}] {res['title']} -> {res['status']}"
              f"{' ' + res['musicbrainz_id'] if res['musicbrainz_id'] else ''}{art}")

    print(f"\nDone. attached={counts['attached']} (with art {counts['with_art']}) "
          f"ambiguous={counts['ambiguous']} no_match={counts['not_found']} "
          f"unreachable={counts.get('error', 0)}")
    if counts.get("aborted"):
        print("  ABORTED on a run of MusicBrainz errors. Nothing was recorded as "
              "missed; re-run when the service is back.")
    if counts["ambiguous"]:
        print("  Ambiguous releases were left alone on purpose -- no art beats wrong art.")


if __name__ == "__main__":
    asyncio.run(main())
