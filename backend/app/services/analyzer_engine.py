"""Lyrical Charger engine — orchestrates parallel lyrics fetch + sequential calibration."""

import asyncio
import logging
from anthropic import Anthropic
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import COLOR_LABELS
from app.services.agents.calibrator import calibrate_song_async, lookup_calibrated, AGENT_MODEL
from app.services.agents.compass_agent_rubric import build_narrative_prompt
from app.services.agents.lyrics_source import fetch_lyrics
from app.services.compass_calc import compute_degree
from app.services.charge_calc import degree_to_charge

logger = logging.getLogger(__name__)


def _build_song_result(index: int, title: str, artist: str, calibration: dict,
                       status: str, lyrics_found: bool) -> dict:
    """Build a standardized song result dict from calibrator output."""
    tier = calibration.get("rubric_color") if status == "scored" else None
    return {
        "index": index,
        "title": title,
        "artist": artist,
        "status": status,
        "tier": tier,
        "tier_label": COLOR_LABELS.get(tier) if tier else None,
        "charge": calibration.get("charge_value") if status == "scored" else None,
        "contaminated": calibration.get("contaminated", False) if status == "scored" else False,
        "contamination_note": calibration.get("contamination_note") if status == "scored" else None,
        "charge_summary": calibration.get("charge_summary") if status == "scored" else None,
        "confidence": calibration.get("confidence", 0.0) if status == "scored" else 0.0,
        "lyrics_found": lyrics_found,
    }


async def fetch_lyrics_async(title: str, artist: str) -> str | None:
    """Wrap sync fetch_lyrics in asyncio.to_thread for parallel I/O."""
    return await asyncio.to_thread(fetch_lyrics, title, artist)


async def run_analysis(session: dict, db: Session, on_event):
    """Run the full analysis pipeline, emitting SSE events via on_event callback.

    on_event(event_type: str, data: dict) is called for each SSE event.
    """
    songs_input = session["songs_input"]
    weighted = session.get("weighted", True)
    total = len(songs_input)

    # 1. Emit session_start
    await on_event("session_start", {"total_songs": total, "status": "processing"})

    # 2. Check cache + parallel lyrics fetch for uncached songs
    cache_results = {}  # index -> calibration dict
    lyrics_needed = []  # (index, title, artist) for songs that need lyrics

    for i, song in enumerate(songs_input):
        cached = lookup_calibrated(song["title"], song["artist"], db)
        if cached:
            cache_results[i] = cached
            logger.info("Analyzer cache hit: %s by %s", song["title"], song["artist"])
        else:
            lyrics_needed.append((i, song["title"], song["artist"]))

    # Fetch lyrics in parallel for uncached songs
    lyrics_map = {}  # index -> lyrics str | None
    if lyrics_needed:
        tasks = [fetch_lyrics_async(title, artist) for _, title, artist in lyrics_needed]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (idx, title, artist), result in zip(lyrics_needed, results):
            if isinstance(result, Exception):
                logger.error("Lyrics fetch error for %s by %s: %s", title, artist, result)
                lyrics_map[idx] = None
            else:
                lyrics_map[idx] = result

    # 3. Assign positions
    # weighted=True: position = index + 1 (first = highest weight)
    # weighted=False: position = 1 for all (equal weight)

    # 4. Sequential calibration + streaming
    all_results = []
    for i, song in enumerate(songs_input):
        title = song["title"]
        artist = song["artist"]

        # Emit song_processing
        await on_event("song_processing", {"index": i, "title": title, "artist": artist})

        if i in cache_results:
            # Cache hit
            result = _build_song_result(i, title, artist, cache_results[i], "scored", True)
            all_results.append(result)
            await on_event("song_result", result)
            continue

        lyrics = lyrics_map.get(i)
        if lyrics is None:
            # No lyrics found
            result = _build_song_result(i, title, artist, {}, "no_lyrics", False)
            all_results.append(result)
            await on_event("song_result", result)
            continue

        # Calibrate via Claude
        try:
            calibration = await calibrate_song_async(title, artist, lyrics, db)
            # If calibrator returned rubric_color=None, that's a parse failure
            if calibration.get("rubric_color") is None:
                result = _build_song_result(i, title, artist, calibration, "error", True)
            else:
                result = _build_song_result(i, title, artist, calibration, "scored", True)
        except Exception:
            logger.exception("Calibration error for %s by %s", title, artist)
            result = _build_song_result(i, title, artist, {}, "error", True)

        all_results.append(result)
        await on_event("song_result", result)

    # 5. Compute aggregate (only scored songs)
    scored = [r for r in all_results if r["status"] == "scored"]
    calibrated_count = len(scored)
    uncalibrated_count = total - calibrated_count

    if scored:
        song_dicts = []
        for r in scored:
            pos = (r["index"] + 1) if weighted else 1
            song_dicts.append({
                "rubric_color": r["tier"],
                "charge_value": r["charge"],
                "position": pos,
            })
        degree = compute_degree(song_dicts)
        charge_color = degree_to_charge(degree)
        charge_score = round((90 - degree) * 100 / 90)
        contam_count = sum(1 for r in scored if r.get("contaminated"))

        # Tier distribution
        tier_dist = {"ascended": 0, "elevated": 0, "decent": 0, "degraded": 0, "corrupted": 0}
        label_to_key = {"Ascended": "ascended", "Elevated": "elevated", "Decent": "decent",
                        "Degraded": "degraded", "Corrupted": "corrupted"}
        for r in scored:
            key = label_to_key.get(r.get("tier_label"), "decent")
            tier_dist[key] += 1

        aggregate = {
            "compass_degree": degree,
            "charge_score": charge_score,
            "charge_level": charge_color,
            "charge_label": COLOR_LABELS.get(charge_color, "Decent"),
            "tier_distribution": tier_dist,
            "contamination_count": contam_count,
            "total_songs": total,
            "calibrated_songs": calibrated_count,
            "uncalibrated_songs": uncalibrated_count,
        }
    else:
        aggregate = {
            "compass_degree": 90.0,
            "charge_score": 0,
            "charge_level": "green",
            "charge_label": "Decent",
            "tier_distribution": {"ascended": 0, "elevated": 0, "decent": 0, "degraded": 0, "corrupted": 0},
            "contamination_count": 0,
            "total_songs": total,
            "calibrated_songs": 0,
            "uncalibrated_songs": uncalibrated_count,
        }

    await on_event("aggregate", aggregate)
    session["aggregate"] = aggregate

    # 6. Generate narrative
    narrative = None
    if scored and settings.anthropic_api_key:
        try:
            narrative = await asyncio.to_thread(_generate_narrative, all_results, aggregate)
        except Exception:
            logger.exception("Failed to generate analyzer narrative")

    if narrative:
        await on_event("narrative", {"text": narrative})
    session["narrative"] = narrative

    # 7. Complete
    session["status"] = "completed"
    await on_event("complete", {"session_id": session["session_id"], "status": "completed"})


def _generate_narrative(song_results: list[dict], aggregate: dict) -> str | None:
    """Generate the personal frequency narrative using Claude."""
    client = Anthropic(api_key=settings.anthropic_api_key)
    system_prompt, user_prompt = build_narrative_prompt(song_results, aggregate)

    response = client.messages.create(
        model=AGENT_MODEL,
        max_tokens=256,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text.strip()
