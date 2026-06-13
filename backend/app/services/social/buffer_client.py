"""Buffer push -- one integration that fans an update out to every connected
RC platform (X, Bluesky, Threads, Instagram, TikTok, Facebook).

Ships DARK: is_configured() is False until social_broadcast_enabled is on AND a
Buffer access token + a profile-id map are set, so the broadcaster runs end to
end (select -> render -> ledger) without ever calling Buffer. When configured,
create_update() posts to the given profiles in one call.

NOTE: this targets Buffer's classic update-create endpoint with a personal
access token. Confirm the exact endpoint + auth shape (and the Free-plan channel
cap) against Buffer's current API when the accounts are connected -- only this
file changes if the shape differs.
"""

import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(
        settings.social_broadcast_enabled
        and settings.buffer_access_token.strip()
        and settings.buffer_profile_ids.strip()
        and profile_map()
    )


def profile_map() -> dict[str, str]:
    """platform -> Buffer profile id, parsed from the JSON env map."""
    raw = settings.buffer_profile_ids.strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("buffer_profile_ids is not valid JSON; no platforms configured")
        return {}
    if not isinstance(parsed, dict):
        logger.warning("buffer_profile_ids JSON is not an object; ignoring")
        return {}
    return {str(k): str(v) for k, v in parsed.items() if v}


async def create_update(text_body: str, image_url: str, profile_ids: list[str]) -> dict:
    """Push one update (text + the card image) to the given Buffer profiles.

    Returns Buffer's raw JSON (`{success, updates: [{id, profile_id, ...}]}`).
    Raises on a non-2xx response.
    """
    if not profile_ids:
        raise RuntimeError("no Buffer profile ids to post to")

    form: list[tuple[str, str]] = [
        ("access_token", settings.buffer_access_token),
        ("text", text_body),
        ("media[photo]", image_url),
        ("media[picture]", image_url),
        ("now", "true"),  # publish straight to the connected channels
    ]
    for pid in profile_ids:
        form.append(("profile_ids[]", pid))

    url = f"{settings.buffer_api_base.rstrip('/')}/updates/create.json"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, data=form)
        resp.raise_for_status()
        return resp.json()


def map_updates_to_platforms(resp: dict, platforms: list[str]) -> dict[str, str]:
    """Reverse Buffer's per-profile update list back to platform -> update id,
    using the configured profile map. Best-effort: unmatched profiles are skipped."""
    pmap = profile_map()
    id_to_platform = {pid: plat for plat, pid in pmap.items()}
    out: dict[str, str] = {}
    for upd in (resp or {}).get("updates", []) or []:
        plat = id_to_platform.get(str(upd.get("profile_id")))
        if plat and upd.get("id"):
            out[plat] = str(upd["id"])
    return out
