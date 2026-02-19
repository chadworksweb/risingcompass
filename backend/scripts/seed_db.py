"""
Load extracted JSON data into the SQLite database.
Run after extract_songs.py and extract_dtmf.py.
"""

import json
import sys
from pathlib import Path

# Add parent to path so we can import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import engine, SessionLocal, Base
from app.models import CompassSong, AlbumDeepDive, AlbumTrack

DATA_DIR = Path(__file__).parent.parent / "data"


def seed_songs(db):
    songs_file = DATA_DIR / "billboard_songs.json"
    if not songs_file.exists():
        print("No billboard_songs.json found — run extract_songs.py first")
        return 0

    with open(songs_file, "r", encoding="utf-8") as f:
        songs_data = json.load(f)

    count = 0
    for s in songs_data:
        song = CompassSong(
            title=s["title"],
            artist=s["artist"],
            year=s["year"],
            decade=s["decade"],
            chart_position=s["chart_position"],
            rubric_color=s["rubric_color"],
            contaminated=s.get("contaminated", False),
            contamination_note=s.get("contamination_note"),
            charge_summary=s.get("charge_summary"),
            why_classification=s.get("why_classification"),
            chart_source=s.get("chart_source", "billboard_hot_100"),
        )
        db.add(song)
        count += 1

    db.commit()
    print(f"Seeded {count} songs")
    return count


def seed_albums(db):
    albums_file = DATA_DIR / "album_deep_dives.json"
    if not albums_file.exists():
        print("No album_deep_dives.json found — run extract_dtmf.py first")
        return 0

    with open(albums_file, "r", encoding="utf-8") as f:
        albums_data = json.load(f)

    count = 0
    for a in albums_data:
        album = AlbumDeepDive(
            title=a["title"],
            artist=a["artist"],
            slug=a["slug"],
            release_year=a.get("release_year"),
            overall_color=a.get("overall_color"),
            summary=a.get("summary"),
        )
        db.add(album)
        db.flush()  # get album.id

        for t in a.get("tracks", []):
            track = AlbumTrack(
                album_id=album.id,
                track_number=t["track_number"],
                name=t["name"],
                charge_color=t.get("charge_color"),
                assessment=t.get("assessment"),
            )
            db.add(track)

        count += 1

    db.commit()
    print(f"Seeded {count} album(s)")
    return count


def main():
    print("Creating tables...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # Clear existing data
        db.query(AlbumTrack).delete()
        db.query(AlbumDeepDive).delete()
        db.query(CompassSong).delete()
        db.commit()

        seed_songs(db)
        seed_albums(db)
    finally:
        db.close()

    print("Done.")


if __name__ == "__main__":
    main()
