"""Seed song_prose_versions from what the songs table still holds.

The version table starts empty, so every already-calibrated song would look like
it had no prose history until its next regen. This gives each one a floor: the
archived `prior_*` text (when present) as the older version, then the live text,
both stamped trigger='backfill_seed'.

It does NOT recover anything already overwritten. Prose pushed out of the single
prior_ slot before migration 145 landed is gone; this only captures what the row
still carries.

Idempotent: a song that already has any version row is skipped, so a re-run after
a partial pass costs nothing.

Usage:
    python scripts/backfill_prose_versions.py --dry-run
    python scripts/backfill_prose_versions.py
    python scripts/backfill_prose_versions.py --limit 500

Local runs need the DB tunnel (see CLAUDE.md > Database); on the droplet run it
inside the backend container.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# (lane, live column, prior column or None, model column or None, generated_at column or None)
LANE_PLAN = (
    ("listener", "listener_effects_prose", "prior_listener_effects_prose", None, None),
    ("societal", "societal_effects_prose", "prior_societal_effects_prose",
     "societal_prose_model", "societal_prose_generated_at"),
    # No archive column ever existed for the bundle, so live only.
    ("psyche_facts", "psyche_facts", None, None, None),
)

SELECT_COLS = (
    "id, title, artist, rubric_color, charge_value, "
    "listener_effects_prose, prior_listener_effects_prose, "
    "societal_effects_prose, prior_societal_effects_prose, "
    "societal_prose_model, societal_prose_generated_at, psyche_facts"
)


def main() -> int:
    p = argparse.ArgumentParser(description="Seed song_prose_versions from the songs table")
    p.add_argument("--dry-run", action="store_true", help="Count only, write nothing.")
    p.add_argument("--limit", type=int, default=None, help="Stop after N songs.")
    args = p.parse_args()

    db = SessionLocal()
    songs_touched = 0
    rows_written = 0
    try:
        already = {
            r[0] for r in db.execute(text("SELECT DISTINCT song_id FROM song_prose_versions")).fetchall()
            if r[0] is not None
        }
        print(f"songs with existing versions: {len(already)}")

        sql = (
            f"SELECT {SELECT_COLS} FROM songs "
            "WHERE listener_effects_prose IS NOT NULL "
            "   OR societal_effects_prose IS NOT NULL "
            "   OR psyche_facts IS NOT NULL "
            "ORDER BY id"
        )
        songs = db.execute(text(sql)).mappings().all()
        print(f"songs carrying prose: {len(songs)}")

        for song in songs:
            if song["id"] in already:
                continue
            if args.limit is not None and songs_touched >= args.limit:
                break

            pending = []
            for lane, live_col, prior_col, model_col, gen_col in LANE_PLAN:
                # Prior first so the ids run oldest to newest.
                for col in ((prior_col, live_col) if prior_col else (live_col,)):
                    if not col:
                        continue
                    val = song[col]
                    if not val or not str(val).strip():
                        continue
                    if pending and pending[-1][1] == val:
                        continue  # prior identical to live: one version, not two
                    pending.append((lane, val, model_col, gen_col))

            if not pending:
                continue
            songs_touched += 1

            if args.dry_run:
                rows_written += len(pending)
                continue

            for lane, val, model_col, gen_col in pending:
                db.execute(
                    text(
                        "INSERT INTO song_prose_versions "
                        "(song_id, title, artist, lane, prose, model, generated_at, "
                        " trigger, rubric_color, charge_value, environment) "
                        "VALUES (:song_id, :title, :artist, :lane, :prose, :model, "
                        " :generated_at, 'backfill_seed', :rubric_color, :charge_value, :environment)"
                    ),
                    {
                        "song_id": song["id"],
                        "title": song["title"],
                        "artist": song["artist"],
                        "lane": lane,
                        "prose": val,
                        "model": song[model_col] if model_col else None,
                        "generated_at": song[gen_col] if gen_col else None,
                        "rubric_color": song["rubric_color"],
                        "charge_value": song["charge_value"],
                        "environment": settings.environment,
                    },
                )
                rows_written += 1

            if songs_touched % 200 == 0:
                db.commit()
                print(f"  ... {songs_touched} songs, {rows_written} rows")

        if not args.dry_run:
            db.commit()
    finally:
        db.close()

    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {rows_written} version rows across {songs_touched} songs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
