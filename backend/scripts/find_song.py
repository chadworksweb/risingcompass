"""Locate a song across all four song tables by title (+ optional artist).

Usage: python find_song.py "t.g.a." [--artist "chad lewine"]

Prints matches with song_source, song_id, title, artist, rubric_color,
charge_value. Useful for one-shot admin workflows (rubric_update
recalibrations, manual investigations).

Runs against whatever DATABASE_URL is configured in backend/.env -- locally
that tunnels to the DO Managed Postgres pool (see CLAUDE.md "Database"); on the
droplet it points at the pool directly. Migrated off libsql/Turso 2026-05-24.
"""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # make `app` importable
load_dotenv(ROOT / ".env")

from app.database import SessionLocal  # noqa: E402
from app.models import CompassSong, LibrarySong, SubmittedSong, StreamSong  # noqa: E402

TABLES = [
    ("compass", CompassSong),
    ("library", LibrarySong),
    ("submitted", SubmittedSong),
    ("stream", StreamSong),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("title")
    p.add_argument("--artist", default=None)
    args = p.parse_args()

    title_like = f"%{args.title.lower()}%"

    db = SessionLocal()
    found_any = False
    try:
        for source, model in TABLES:
            q = db.query(model).filter(func.lower(model.title).like(title_like))
            if args.artist:
                q = q.filter(func.lower(model.artist).like(f"%{args.artist.lower()}%"))
            rows = q.order_by(model.id.desc()).limit(20).all()
            if rows:
                found_any = True
                print(f"\n[{source}]")
                for r in rows:
                    color = r.rubric_color or "-"
                    charge = r.charge_value if r.charge_value is not None else "-"
                    print(f"  id={r.id:<5} color={color:<7} charge={charge!s:<5}  {r.title}  --  {r.artist}")
    finally:
        db.close()

    if not found_any:
        print("No matches found.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
