"""Buffer push -- one integration that fans an update out to every connected
RC channel (X, Bluesky, Threads, Instagram, TikTok, Facebook).

Targets Buffer's CURRENT GraphQL API (https://api.buffer.com). The classic REST
API (api.bufferapp.com/1/updates/create + an access_token form field) is closed
to new accounts, so this posts via the GraphQL `createPost` mutation with a
personal API key in the Authorization header. Two shape differences from the
classic API the rest of the package is insulated from:
  - GraphQL takes ONE channelId per createPost, so create_update() loops the
    configured channels and issues one mutation each.
  - It returns the same synthetic `{success, updates:[{id, profile_id}], errors}`
    shape the classic endpoint did (profile_id == channel id), so
    map_updates_to_platforms() and the broadcaster are unchanged.

Ships DARK: is_configured() is False until social_broadcast_enabled is on AND a
Buffer access token + a channel-id map are set, so the broadcaster runs end to
end (select -> render -> ledger) without ever calling Buffer.

NOTE: the GraphQL API is in public beta. This client is written against its
documented schema; smoke-test one real post at go-live (the first configured
run) and adjust the mutation only if Buffer's scalar/input names have moved.
"""

import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# One createPost per channel, published immediately with the card image attached
# by public URL. `automatic` scheduling = Buffer publishes directly to the
# connected channel (vs `notification`, a mobile reminder to post by hand).
# `metadata` carries the per-service required fields (e.g. Instagram demands a
# post type); it is null for channels that need none.
_CREATE_POST = """
mutation CreatePost($channelId: ChannelId!, $text: String!, $assets: [AssetInput!]!, $metadata: PostInputMetaData) {
  createPost(input: {
    channelId: $channelId
    text: $text
    schedulingType: automatic
    mode: shareNow
    assets: $assets
    metadata: $metadata
  }) {
    __typename
    ... on PostActionSuccess { post { id } }
    ... on MutationError { message }
  }
}
""".strip()


def _platform_metadata(platform: str) -> dict | None:
    """Per-service createPost metadata. Instagram requires an explicit post type
    (post|story|reel; a plain feed image is `post`) AND shouldShareToFeed (a
    required Boolean -- true publishes to the main feed). Others need none (so far)."""
    if platform == "instagram":
        return {"instagram": {"type": "post", "shouldShareToFeed": True}}
    return None


def is_configured() -> bool:
    return bool(
        settings.social_broadcast_enabled
        and settings.buffer_access_token.strip()
        and settings.buffer_profile_ids.strip()
        and profile_map()
    )


def profile_map() -> dict[str, str]:
    """platform -> Buffer channel id, parsed from the JSON env map."""
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


async def _create_post(client: httpx.AsyncClient, channel_id: str, text_body: str,
                       image_urls: list[str], metadata: dict | None = None) -> dict:
    """Issue one createPost mutation for a single channel. image_urls is an
    ordered list of publicly-fetchable image URLs: one = a single image post,
    several = a carousel (Instagram/TikTok). Returns {"id": <post id>} on success
    or {"error": <message>} on a handled failure."""
    assets = [{"image": {"url": u}} for u in image_urls if u]
    payload = {
        "query": _CREATE_POST,
        "variables": {
            "channelId": channel_id,
            "text": text_body,
            "assets": assets,
            "metadata": metadata,
        },
    }
    resp = await client.post("", json=payload)
    resp.raise_for_status()
    body = resp.json()

    # GraphQL returns HTTP 200 even for query/validation errors -- surface them.
    if body.get("errors"):
        msg = "; ".join(e.get("message", "") for e in body["errors"] if isinstance(e, dict))
        return {"error": msg or "graphql error"}

    result = (body.get("data") or {}).get("createPost") or {}
    post = result.get("post") or {}
    if post.get("id"):
        return {"id": str(post["id"])}
    return {"error": result.get("message") or "createPost returned no post"}


async def post_items(items: list[dict]) -> dict:
    """Push one post PER platform. Each item is
    {"platform", "channel_id", "text", "image_urls": [...]} -- a single image or a
    carousel. Returns {"posted": {platform: post_id}, "errors": {platform: msg}}.
    Per-platform failures are collected (a partial run still records what landed);
    only a missing token/empty items raises.
    """
    if not items:
        return {"posted": {}, "errors": {}}

    base = settings.buffer_api_base.rstrip("/")
    headers = {
        "Authorization": f"Bearer {settings.buffer_access_token}",
        "Content-Type": "application/json",
    }
    posted: dict[str, str] = {}
    errors: dict[str, str] = {}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=60) as client:
        for it in items:
            platform = it["platform"]
            metadata = _platform_metadata(platform)
            try:
                outcome = await _create_post(
                    client, it["channel_id"], it["text"], it["image_urls"], metadata
                )
            except httpx.HTTPError as exc:
                outcome = {"error": str(exc)}
            if outcome.get("id"):
                posted[platform] = outcome["id"]
            else:
                errors[platform] = outcome.get("error") or "buffer push failed"
                logger.warning("Buffer createPost failed for %s: %s", platform, errors[platform])
    return {"posted": posted, "errors": errors}
