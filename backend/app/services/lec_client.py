"""HTTP client for the Libra Engine Compass (LEC) scoring service.

RC consumes LEC's `POST /api/score` as the single scoring brain. The calibrator
was extracted into its own service so feature work stops colliding with
calibrator tuning; this module maps LEC's score response back into RC's exact
in-process calibration dict (the shape calibrate_song_async builds BEFORE
_ensure_generation), so the rest of the path -- the verbatim-quote guard,
generation, and persistence -- runs unchanged on the result.

Fail-soft by contract: ANY problem (missing config, transport error, non-200,
unscorable read, malformed body) returns None so the caller falls back to RC's
in-process calibrator. LEC sits on the live scoring path, so it must never
harden a failure into a user-facing error while the in-process path is a working
fallback.
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def score_via_lec(
    title: str,
    artist: str,
    lyrics: str,
    artifact_type: str = "lyric",
) -> dict | None:
    """POST one artifact to LEC /api/score and map the response into RC's
    calibration-dict shape (the same keys the in-process calibrator emits before
    enrichment). Returns None on any failure so the caller scores in-process."""
    base = (settings.lec_base_url or "").rstrip("/")
    if not base:
        return None

    payload = {
        "type": artifact_type,
        "text": lyrics,
        "title": title or "",
        "artist": artist or "",
        # RC library reads use precedents (decision #4). The corpus is not synced
        # yet (Phase 1), so LEC scores statelessly for now and ignores this true.
        "use_precedents": True,
    }
    headers = {}
    if settings.lec_api_key:
        headers["X-Api-Key"] = settings.lec_api_key

    try:
        async with httpx.AsyncClient(timeout=settings.lec_timeout_seconds) as client:
            resp = await client.post(f"{base}/api/score", json=payload, headers=headers)
    except Exception:
        logger.warning("LEC /api/score request failed for %s / %s; falling back in-process",
                       title, artist, exc_info=True)
        return None

    if resp.status_code != 200:
        logger.warning("LEC /api/score returned %s for %s / %s; falling back in-process",
                       resp.status_code, title, artist)
        return None

    try:
        data = resp.json()
    except Exception:
        logger.warning("LEC /api/score returned non-JSON for %s / %s; falling back in-process",
                       title, artist)
        return None

    if data.get("status") != "scored" or data.get("color_key") is None:
        # Unscorable / null read -> fall back to the in-process calibrator, which
        # may succeed (a transient LEC parse failure) or return its own explicit
        # null. Never default a tier here.
        logger.info("LEC scored %s / %s as %s; falling back in-process",
                    title, artist, data.get("status"))
        return None

    comp = data.get("components") or {}
    # Map LEC's response onto RC's calibration dict. The keys + nested shapes
    # (harm {value, pervasive}, transcendence {value}) match what compose() emits
    # in process, so RC's calibration_runs row is lossless and the song page
    # renders identically regardless of which engine scored it.
    return {
        "rubric_color": data.get("color_key"),
        "charge_value": data.get("charge_value"),
        "contaminated": bool(data.get("contaminated", False)),
        "contamination_note": data.get("contamination_note"),
        "dogma_referenced": bool(data.get("dogma_referenced", False)),
        "dogma_note": data.get("dogma_note"),
        "charge_summary": data.get("charge_summary", ""),
        "confidence": data.get("confidence", 0.0),
        "reasoning": comp.get("reasoning"),
        # v3 components -> calibration_runs columns (log_run). Internal-only;
        # song_sync's column whitelist keeps them off the songs row.
        "visceral_charge": comp.get("visceral_charge"),
        "route": comp.get("route"),
        "harm": comp.get("harm"),
        "transcendence": comp.get("transcendence"),
        "governing_axis": comp.get("governing_axis"),
        "center": comp.get("center"),
        "vernier": comp.get("vernier"),
        "precedent_refs": data.get("precedent_refs"),
        "gut_divergence": comp.get("gut_divergence"),
        "guard_trips": comp.get("guard_trips"),
        "parse_retries": comp.get("parse_retries"),
        "escalation_flags": comp.get("escalation_flags"),
        "escalated": comp.get("escalated"),
    }
