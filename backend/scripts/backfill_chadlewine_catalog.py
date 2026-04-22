"""One-shot backfill: run chadlewine.com's full song catalog through the RC
calibrator, writing submitted_songs rows with source="chadlewine-backfill"
and updating consensus via record_and_reconcile.

Bypasses HTTP / slowapi / auth entirely — calls calibrate_song_async
in-process with a shared DB session. Run inside the container:

    docker compose exec backend python scripts/backfill_chadlewine_catalog.py \
        --input /tmp/chadlewine_songs.json \
        --output /tmp/chadlewine_results.json

Input JSON schema (array):
    [{"id": "<chadlewine_song_uuid>", "title": "...", "lyrics": "..."}, ...]

Output JSON schema: run metadata + per-song result keyed on chadlewine id,
ready for the chadlewine-side ingest pass.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception:
    pass

from app.database import SessionLocal
from app.models import SubmittedSong
from app.services.agents.calibrator import calibrate_song_async
from app.services.artist_linker import try_link_song
from app.services.calibration_corpus import (
    record_and_reconcile, hash_lyrics, find_canonical_song,
)
from app.routers.analyzer import _validate_lyrics, detect_prose_like


SOURCE_TAG = "chadlewine-backfill"
TRIGGERED_BY = "backfill_chadlewine_catalog"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill")


async def calibrate_with_retry(title: str, artist: str, lyrics: str, db, retries: int) -> dict:
    """Call the calibrator, retrying on rubric_color=None up to `retries` times."""
    last_result: dict = {}
    for attempt in range(retries + 1):
        try:
            result = await calibrate_song_async(title, artist, lyrics, db, skip_cache=True)
        except Exception:
            logger.exception("Calibrator raised on attempt %d/%d", attempt + 1, retries + 1)
            result = {}
        last_result = result
        if result.get("rubric_color") is not None:
            return result
        if attempt < retries:
            await asyncio.sleep(2.0)
    return last_result


async def process_one(
    song: dict,
    artist: str,
    db,
) -> dict:
    """Returns a per-song result dict — {song_id, title, status, ...}."""
    song_id = song.get("id")
    title = (song.get("title") or "").strip()
    lyrics = song.get("lyrics") or ""

    if not title:
        return {"song_id": song_id, "title": title, "status": "skipped", "reason": "missing_title"}

    validation_error = _validate_lyrics(lyrics)
    if validation_error:
        return {"song_id": song_id, "title": title, "status": "skipped", "reason": validation_error}

    prose_reason = detect_prose_like(lyrics)
    if prose_reason:
        return {"song_id": song_id, "title": title, "status": "skipped", "reason": f"prose: {prose_reason}"}

    calibration = await calibrate_with_retry(title, artist, lyrics, db, retries=3)

    color = calibration.get("rubric_color")
    if color is None:
        return {"song_id": song_id, "title": title, "status": "failed", "reason": "calibrator_returned_no_color"}

    try:
        pre_canonical = find_canonical_song(title, artist, db)

        submitted = SubmittedSong(
            title=title,
            artist=artist,
            rubric_color=color,
            charge_value=calibration.get("charge_value"),
            contaminated=calibration.get("contaminated", False),
            contamination_note=calibration.get("contamination_note"),
            dogma_referenced=bool(calibration.get("dogma_referenced", False)),
            dogma_note=calibration.get("dogma_note"),
            charge_summary=calibration.get("charge_summary"),
            confidence=calibration.get("confidence"),
            source=SOURCE_TAG,
            ip_address=None,
        )
        db.add(submitted)
        db.commit()
        db.refresh(submitted)

        try_link_song(title, artist, "submitted", submitted.id, db)

        if pre_canonical:
            record_and_reconcile(
                db,
                title=title,
                artist=artist,
                calibration=calibration,
                triggered_by=TRIGGERED_BY,
                lyrics_hash=hash_lyrics(lyrics),
                agent_model=calibration.get("agent_model"),
                direct_song_source=pre_canonical[0],
                direct_song_id=pre_canonical[1].id,
                is_new_row=False,
            )
        else:
            record_and_reconcile(
                db,
                title=title,
                artist=artist,
                calibration=calibration,
                triggered_by=TRIGGERED_BY,
                lyrics_hash=hash_lyrics(lyrics),
                agent_model=calibration.get("agent_model"),
                direct_song_source="submitted",
                direct_song_id=submitted.id,
                is_new_row=True,
            )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("DB write failed for %s", title)
        return {"song_id": song_id, "title": title, "status": "failed", "reason": f"db_error: {e}"}

    return {
        "song_id": song_id,
        "title": title,
        "status": "ok",
        "tier": color,
        "charge": calibration.get("charge_value"),
        "charge_summary": calibration.get("charge_summary"),
        "contaminated": calibration.get("contaminated", False),
        "confidence": calibration.get("confidence"),
    }


async def main_async(args) -> int:
    with open(args.input, encoding="utf-8") as f:
        songs = json.load(f)

    if not isinstance(songs, list):
        logger.error("Input must be a JSON array — got %s", type(songs).__name__)
        return 2

    if args.limit > 0:
        songs = songs[: args.limit]

    logger.info("Loaded %d songs from %s", len(songs), args.input)
    logger.info("Artist: %s | source_tag: %s | sleep: %.1fs", args.artist, SOURCE_TAG, args.sleep)

    results: list[dict[str, Any]] = []
    counts = {"ok": 0, "skipped": 0, "failed": 0}

    db = SessionLocal()
    try:
        for i, song in enumerate(songs, start=1):
            title_preview = (song.get("title") or "<no title>")[:60]
            print(f"[{i}/{len(songs)}] {title_preview} ... ", end="", flush=True)
            result = await process_one(song, args.artist, db)
            results.append(result)
            counts[result["status"]] = counts.get(result["status"], 0) + 1

            if result["status"] == "ok":
                charge = result.get("charge")
                charge_str = f"+{charge}" if isinstance(charge, int) and charge > 0 else str(charge)
                print(f"{result['tier']} / {charge_str}")
            else:
                print(f"{result['status'].upper()}: {result.get('reason', '')}")

            if i < len(songs):
                await asyncio.sleep(args.sleep)
    finally:
        db.close()

    payload = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "source_tag": SOURCE_TAG,
        "artist": args.artist,
        "input_path": os.path.abspath(args.input),
        "total": len(songs),
        "counts": counts,
        "results": results,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    logger.info(
        "Done. ok=%d skipped=%d failed=%d | wrote %s",
        counts.get("ok", 0), counts.get("skipped", 0), counts.get("failed", 0),
        args.output,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to input songs JSON array")
    parser.add_argument("--output", required=True, help="Path to write results JSON")
    parser.add_argument("--artist", default="Chad Lewine", help='Artist name to attach to every song (default: "Chad Lewine")')
    parser.add_argument("--sleep", type=float, default=1.0, help="Seconds to sleep between songs (default: 1.0)")
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N songs (0 = all)")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
