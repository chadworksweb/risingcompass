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


def group_songs_by_album(export_path: str, cl_to_rc: dict) -> tuple[dict[str, dict], list[dict]]:
    """Returns (albums, singles).

    `albums`  = {album_id: {title, release_date, songs: [...]}} for every
                song that belongs to at least one album.
    `singles` = [{title, release_date, song: {...}}, ...] for each orphan
                song where is_single=True and release_date is set. These
                become standalone Release(release_type="single") records.

    Songs that are neither on an album nor a flagged single (truly orphan,
    no release metadata) are left for the catch-all.
    """
    with open(export_path, encoding="utf-8") as f:
        export = json.load(f)

    albums: dict[str, dict] = {}
    singles: list[dict] = []

    for s in export["songs"]:
        rc_ref = cl_to_rc.get(s["id"])
        song_payload = {
            "cl_id": s["id"],
            "title": s["title"],
            "rc_source": rc_ref[0] if rc_ref else None,
            "rc_id": rc_ref[1] if rc_ref else None,
            "instrumental": s.get("instrumental", False),
        }

        album_memberships = s.get("albums") or []
        if album_memberships:
            for album in album_memberships:
                aid = album["album_id"]
                if aid not in albums:
                    albums[aid] = {
                        "album_id": aid,
                        "title": album["title"],
                        "release_date": album.get("release_date"),
                        "songs": [],
                    }
                albums[aid]["songs"].append({
                    **song_payload,
                    "track_number": album.get("track_number"),
                })
        elif s.get("is_single") and s.get("release_date"):
            singles.append({
                "title": s["title"],
                "release_date": s["release_date"],
                "song": song_payload,
            })
        # else: truly orphan — stays in the catch-all

    return albums, singles


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

    albums, singles = group_songs_by_album(args.full_export, cl_to_rc)
    logger.info("Grouped %d albums / EPs and %d standalone singles",
                len(albums), len(singles))

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

        songs_that_belong_to_real_releases: set[tuple[str, int]] = set()
        for album in albums.values():
            for song in album["songs"]:
                if song["rc_source"] and song["rc_id"]:
                    songs_that_belong_to_real_releases.add((song["rc_source"], song["rc_id"]))
        for single in singles:
            song = single["song"]
            if song["rc_source"] and song["rc_id"]:
                songs_that_belong_to_real_releases.add((song["rc_source"], song["rc_id"]))

        created = []
        updated = []
        created_singles = []
        updated_singles = []

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

        # Standalone singles — one Release per song.
        for single in singles:
            title = single["title"]
            song = single["song"]
            rd = single["release_date"]
            rel_date = None
            rel_year = None
            try:
                rel_date = date.fromisoformat(rd)
                rel_year = rel_date.year
            except (ValueError, TypeError):
                logger.warning("Bad release_date '%s' on single '%s'", rd, title)

            existing = (
                db.query(Release)
                .filter(Release.artist_id == artist.id)
                .filter(Release.title == title)
                .first()
            )
            if existing:
                existing.release_type = "single"
                existing.release_date = rel_date
                existing.release_year = rel_year
                release = existing
                updated_singles.append(title)
            else:
                release = Release(
                    artist_id=artist.id,
                    title=title,
                    release_type="single",
                    release_date=rel_date,
                    release_year=rel_year,
                )
                db.add(release)
                db.flush()
                created_singles.append(title)

            db.query(ReleaseSong).filter(ReleaseSong.release_id == release.id).delete()

            charge_value = None
            contam = False
            calibrated = 0
            if song["rc_source"] and song["rc_id"]:
                db.add(ReleaseSong(
                    release_id=release.id,
                    song_source=song["rc_source"],
                    song_id=song["rc_id"],
                    track_number=1,
                ))
                Model = _SONG_MODEL_MAP.get(song["rc_source"])
                if Model:
                    row = db.query(Model).filter(Model.id == song["rc_id"]).first()
                    if row is not None:
                        charge_value = row.charge_value
                        contam = bool(getattr(row, "contaminated", False))
                        if charge_value is not None:
                            calibrated = 1

            release.track_count = 1
            release.calibrated_count = calibrated
            release.contamination_count = 1 if contam else 0
            if charge_value is not None:
                result = compute_release_charge([charge_value])
                if result:
                    release.charge_value = result[0]
                    release.rubric_color = result[1]
            else:
                release.charge_value = None
                release.rubric_color = None

        # Clean up catch-all: drop any ReleaseSong whose (source, id) now
        # belongs to a real album or single release; recompute aggregates.
        moved = 0
        if catch_all:
            stale_links = (
                db.query(ReleaseSong)
                .filter(ReleaseSong.release_id == catch_all.id)
                .all()
            )
            for link in stale_links:
                if (link.song_source, link.song_id) in songs_that_belong_to_real_releases:
                    db.delete(link)
                    moved += 1
            db.flush()
            recompute_catchall_aggregates(db, catch_all)

        if args.dry_run:
            logger.info("DRY RUN — rolling back")
            db.rollback()
        else:
            db.commit()

        logger.info("Albums/EPs: %d created, %d updated", len(created), len(updated))
        if created: logger.info("  created: %s", created)
        if updated: logger.info("  updated: %s", updated)
        logger.info("Singles: %d created, %d updated",
                    len(created_singles), len(updated_singles))
        if created_singles: logger.info("  created: %s", created_singles)
        if updated_singles: logger.info("  updated: %s", updated_singles)
        logger.info("Moved %d song-links out of catch-all into real releases", moved)

    finally:
        db.close()


if __name__ == "__main__":
    main()
