"""One-shot: materialize chadlewine.com's album structure in RC.

Creates Release + ReleaseSong records so the Chad Lewine artist page stops
collapsing everything into "Singles & Uncategorized". Uses two JSON inputs:

  --full-export    chadlewine's songs-with-album-memberships export
                   (chadlewine_songs_full_export_w_RC_data.json)

  --results-merged the calibration backfill's output, which maps each
                   chadlewine song_id → rc_song_source + rc_song_id
                   (chadlewine_results_merged.json)

Running inside the container (preferred — same DB path as the prior
calibration backfill):

    docker compose cp /path/to/chadlewine_songs_full_export_w_RC_data.json \\
        rc-backend:/tmp/full.json
    docker compose cp /path/to/chadlewine_results_merged.json \\
        rc-backend:/tmp/merged.json
    docker compose exec backend python scripts/backfill_chadlewine_releases.py \\
        --full-export /tmp/full.json \\
        --results-merged /tmp/merged.json \\
        [--dry-run]

Idempotent by (artist_id, title): re-running updates releases in place and
rebuilds their ReleaseSong links from scratch (so track ordering and
track_number stay in sync if the chadlewine data shifts).
"""

import argparse
import json
import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception:
    pass

from app.database import SessionLocal
from app.models import (
    Artist, Release, ReleaseSong,
    CompassSong, LibrarySong, SubmittedSong,
)
from app.services.artist_utils import compute_release_charge


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("cl-releases")


ARTIST_SLUG = "chad-lewine"
CATCH_ALL_TITLE = "Singles & Uncategorized"

_SONG_MODEL_MAP = {
    "compass": CompassSong,
    "library": LibrarySong,
    "submitted": SubmittedSong,
}


def classify_release_type(track_count: int) -> str:
    """Heuristic because chadlewine's album export doesn't include format_slug.
    Chad can hand-correct in the admin UI afterwards if needed."""
    if track_count >= 8:
        return "album"
    if track_count >= 3:
        return "ep"
    return "single"


def load_cl_to_rc_map(results_path: str) -> dict[str, tuple[str, int]]:
    with open(results_path, encoding="utf-8") as f:
        results = json.load(f)
    out: dict[str, tuple[str, int]] = {}
    for r in results["results"]:
        if r.get("rc_song_id") and r.get("rc_song_source"):
            out[r["song_id"]] = (r["rc_song_source"], int(r["rc_song_id"]))
    return out


def group_songs_by_album(export_path: str, cl_to_rc: dict) -> dict[str, dict]:
    """Returns {album_id: {title, release_date, songs: [...]}}.

    Each song dict: cl_id, title, track_number, rc_source, rc_id, charge,
    instrumental.
    """
    with open(export_path, encoding="utf-8") as f:
        export = json.load(f)

    albums: dict[str, dict] = {}
    for s in export["songs"]:
        for album in (s.get("albums") or []):
            aid = album["album_id"]
            if aid not in albums:
                albums[aid] = {
                    "album_id": aid,
                    "title": album["title"],
                    "release_date": album.get("release_date"),
                    "songs": [],
                }
            rc_ref = cl_to_rc.get(s["id"])
            rc_data = s.get("rising_compass") or {}
            albums[aid]["songs"].append({
                "cl_id": s["id"],
                "title": s["title"],
                "track_number": album.get("track_number"),
                "rc_source": rc_ref[0] if rc_ref else None,
                "rc_id": rc_ref[1] if rc_ref else None,
                "charge": rc_data.get("charge"),
                "instrumental": s.get("instrumental", False),
            })
    return albums


def recompute_catchall_aggregates(db, catch_all: Release) -> None:
    """Walk remaining ReleaseSong links on the catch-all and recompute
    track_count, calibrated_count, charge_value, rubric_color."""
    links = db.query(ReleaseSong).filter(ReleaseSong.release_id == catch_all.id).all()
    catch_all.track_count = len(links)

    charges: list[int] = []
    contam = 0
    for link in links:
        Model = _SONG_MODEL_MAP.get(link.song_source)
        if not Model:
            continue
        row = db.query(Model).filter(Model.id == link.song_id).first()
        if row is None:
            continue
        if row.charge_value is not None:
            charges.append(row.charge_value)
        if getattr(row, "contaminated", False):
            contam += 1

    catch_all.calibrated_count = len(charges)
    catch_all.contamination_count = contam
    if charges:
        result = compute_release_charge(charges)
        if result:
            catch_all.charge_value = result[0]
            catch_all.rubric_color = result[1]
    else:
        catch_all.charge_value = None
        catch_all.rubric_color = None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full-export", required=True)
    ap.add_argument("--results-merged", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cl_to_rc = load_cl_to_rc_map(args.results_merged)
    logger.info("Loaded %d chadlewine → RC mappings", len(cl_to_rc))

    albums = group_songs_by_album(args.full_export, cl_to_rc)
    logger.info("Grouped %d distinct albums", len(albums))

    db = SessionLocal()
    try:
        artist = db.query(Artist).filter(Artist.slug == ARTIST_SLUG).first()
        if not artist:
            raise SystemExit(f"Artist '{ARTIST_SLUG}' not found in RC")
        logger.info("Artist: %s (id=%d)", artist.name, artist.id)

        catch_all = (
            db.query(Release)
            .filter(Release.artist_id == artist.id)
            .filter(Release.title == CATCH_ALL_TITLE)
            .first()
        )

        songs_that_belong_to_albums: set[tuple[str, int]] = set()
        for album in albums.values():
            for song in album["songs"]:
                if song["rc_source"] and song["rc_id"]:
                    songs_that_belong_to_albums.add((song["rc_source"], song["rc_id"]))

        created = []
        updated = []

        for album in albums.values():
            title = album["title"]
            total_tracks = len(album["songs"])

            rd = album["release_date"]
            rel_date = None
            rel_year = None
            if rd:
                try:
                    rel_date = date.fromisoformat(rd)
                    rel_year = rel_date.year
                except ValueError:
                    logger.warning("Bad release_date '%s' on album '%s'", rd, title)

            release_type = classify_release_type(total_tracks)

            existing = (
                db.query(Release)
                .filter(Release.artist_id == artist.id)
                .filter(Release.title == title)
                .first()
            )
            if existing:
                existing.release_type = release_type
                existing.release_date = rel_date
                existing.release_year = rel_year
                release = existing
                updated.append(title)
            else:
                release = Release(
                    artist_id=artist.id,
                    title=title,
                    release_type=release_type,
                    release_date=rel_date,
                    release_year=rel_year,
                )
                db.add(release)
                db.flush()
                created.append(title)

            # Rebuild links for this release from scratch.
            db.query(ReleaseSong).filter(ReleaseSong.release_id == release.id).delete()

            charges: list[int] = []
            calibrated = 0
            contam = 0
            songs_sorted = sorted(
                album["songs"],
                key=lambda s: (s["track_number"] is None, s["track_number"] or 0),
            )
            for song in songs_sorted:
                if not (song["rc_source"] and song["rc_id"]):
                    continue
                db.add(ReleaseSong(
                    release_id=release.id,
                    song_source=song["rc_source"],
                    song_id=song["rc_id"],
                    track_number=song["track_number"],
                ))
                # Pull charge + contamination from the live RC row (source of
                # truth), not the JSON — the two should agree but RC wins.
                Model = _SONG_MODEL_MAP.get(song["rc_source"])
                if Model:
                    row = db.query(Model).filter(Model.id == song["rc_id"]).first()
                    if row is not None and row.charge_value is not None:
                        charges.append(row.charge_value)
                        calibrated += 1
                    if row is not None and getattr(row, "contaminated", False):
                        contam += 1

            release.track_count = total_tracks
            release.calibrated_count = calibrated
            release.contamination_count = contam
            if charges:
                result = compute_release_charge(charges)
                if result:
                    release.charge_value = result[0]
                    release.rubric_color = result[1]
                else:
                    release.charge_value = None
                    release.rubric_color = None
            else:
                release.charge_value = None
                release.rubric_color = None

        # Clean up catch-all: drop any ReleaseSong whose (source, id) is now
        # owned by a real album; recompute aggregates.
        moved = 0
        if catch_all:
            stale_links = (
                db.query(ReleaseSong)
                .filter(ReleaseSong.release_id == catch_all.id)
                .all()
            )
            for link in stale_links:
                if (link.song_source, link.song_id) in songs_that_belong_to_albums:
                    db.delete(link)
                    moved += 1
            db.flush()
            recompute_catchall_aggregates(db, catch_all)

        if args.dry_run:
            logger.info("DRY RUN — rolling back")
            db.rollback()
        else:
            db.commit()

        logger.info("Created %d releases: %s", len(created), created)
        logger.info("Updated %d releases: %s", len(updated), updated)
        logger.info("Moved %d song-links out of catch-all into real albums", moved)

    finally:
        db.close()


if __name__ == "__main__":
    main()
