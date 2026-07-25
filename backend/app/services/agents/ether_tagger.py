"""Ether tagger — names what a compass_song IS through the Ether Art Chart lens.

Single-shot Opus call run after the main calibrator. Emits a flat literal
deadpan_line plus 0-3 ranked topic slugs from the closed 25-topic taxonomy
(`services/ether_taxonomy.ETHER_TAXONOMY`). When no honest taxonomy match
exists, returns an audit payload instead of forcing a bad fit.

Fails soft — any unrecoverable error returns None and the caller leaves the
three ether columns NULL on the compass_song row.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from anthropic import Anthropic

from app.config import settings
from app.services.claude_meter import tracked_create
from app.services import ether_taxonomy as tax
from app.services.ether_taxonomy import VALID_SLUGS

logger = logging.getLogger(__name__)

AGENT_MODEL = settings.agent_model

MAX_TOPICS = 3
# Flat cap on deadpan_line in characters. The previous heuristic
# (2× artist+title length) chopped legitimate deadpans whenever the
# title+artist were short — e.g. "Jane!" by "The Long Faces" gave
# a 38-char cap and truncated "Gothic character sketch of a [...]"
# mid-sentence. Every clean deadpan in the corpus today is ≤42 chars,
# so 80 is generous headroom while still enforcing the museum-placard
# brevity the prompt asks for.
MAX_DEADPAN_LENGTH = 80


_VOICE_BLOCK = """You are the Ether Tagger for The Rising Compass.

Your job: given a song's lyrics and existing calibrator outputs, emit two
pieces of data that name what the song IS:

  1. deadpan_line — a flat, literal naming of the song's content,
     approximately len(artist) + len(title) characters long.

  2. topics — 0 to 3 tags from the closed taxonomy below, ordered
     most-dominant-first. If no honest match exists in the taxonomy,
     return topics: [] and a topic_audit payload.

You are an instrument. You name. You do not judge, joke, or comment.

═══════════════════════════════════════════════════════════════════════
VOICE — read this twice
═══════════════════════════════════════════════════════════════════════

The deadpan_line is FLAT. Museum-placard register. Naming, not commenting.

  • Descriptive adjectives are allowed: "lusty", "wounded", "defiant".
  • Evaluative adjectives are forbidden: "trashy", "shallow", "vapid",
    "pathetic". These are verdicts, not names.
  • Describe the LYRICS, never the artist. "Revenge breakup song" is
    about the song. "X is a vengeful person" is about the artist —
    forbidden.
  • The line should land flat. If the audience laughs, that's the gap
    between the song's marketing and its content showing — not a joke
    you made.

Length: ~ len(artist) + len(title). Single fragment. No period. No
articles if they can be dropped. One per song.

Examples of the register (read these flat, not winking):
  • "Revenge breakup song"
  • "Lusty flirty proclamation"
  • "Ego trip on drugs"
  • "Wounded-pride retaliation track"
  • "Hometown-longing ballad"
  • "Substance-soaked party hymn"
  • "Status-anxiety flex"
  • "Grief-processing eulogy"
  • "Coming-of-age small-town reckoning"
  • "Possessive-jealousy spiral"
  • "Faith-shaken reckoning"
  • "Power-fantasy boast"
  • "Reconciliation-attempt confession"
  • "Loyalty-oath toast"
  • "Carnal-encounter narrative"
  • "Money-as-armor manifesto"
  • "Heartbreak-tinged nostalgia"
  • "Burnout confession"
  • "Doomed-romance lament"
  • "Defiant self-affirmation chant"
"""

_TOPIC_RANKING_BLOCK = """═══════════════════════════════════════════════════════════════════════
TOPIC RANKING RULE
═══════════════════════════════════════════════════════════════════════

Order topics by lyrical centrality:
  1. The topic that captures the song's DOMINANT ARC comes first.
  2. Secondary themes follow, by share of the song's content.
  3. If two topics are roughly equal in weight, the more specific one
     wins (e.g., "betrayal" outranks "breakup" if the song's focus is
     specifically the betrayal).
  4. Cap at 3. Songs almost never carry more than 3 honest topics. If
     you find yourself wanting 4, you're tagging coincidental mentions,
     not actual themes — drop the weakest.
"""

_AUDIT_BLOCK = """═══════════════════════════════════════════════════════════════════════
AUDIT ESCAPE HATCH — mandatory when nothing fits
═══════════════════════════════════════════════════════════════════════

If you cannot honestly tag the song with even one topic from the
25-topic list, return:

  topics: []
  topic_audit: {
    "reason": "no match found",
    "proposed_tag": "short-slug-suggestion",
    "rationale": "one sentence on why none of the listed topics fit and what
                  the new tag would capture"
  }

Forcing a bad fit corrupts the data. Audit is a feature, not a failure.
"""

_OUTPUT_BLOCK = """═══════════════════════════════════════════════════════════════════════
OUTPUT — strict JSON
═══════════════════════════════════════════════════════════════════════

Return ONLY this JSON object. No prose before or after.

{
  "deadpan_line": "string",
  "topics": ["slug", ...],     // 0-3 entries from the taxonomy
  "topic_audit": null OR {     // null when topics is non-empty
    "reason": "...",
    "proposed_tag": "...",
    "rationale": "..."
  }
}

Invariants:
  • If topics is empty, topic_audit MUST be a non-null object.
  • If topics is non-empty, topic_audit MUST be null.
  • All slugs in topics MUST appear in the taxonomy list. No invented
    slugs in topics — if it's not on the list, use the audit hatch.
"""

_FEW_SHOT_BLOCK = """═══════════════════════════════════════════════════════════════════════
WORKED EXAMPLES
═══════════════════════════════════════════════════════════════════════

─── Example 1 ─────────────────────────────────────────────────────────
Title: Yesterday
Artist: The Beatles
charge_summary: "Quiet grief over a lost love and self-blame for what went wrong."

Output:
{
  "deadpan_line": "Wistful self-blaming heartbreak",
  "topics": ["breakup", "grief", "longing"],
  "topic_audit": null
}

─── Example 2 ─────────────────────────────────────────────────────────
Title: Started From The Bottom
Artist: Drake
charge_summary: "Self-congratulating origin story; team loyalty layered with materialist accomplishment."

Output:
{
  "deadpan_line": "Origin-story flex with crew",
  "topics": ["flex", "ambition", "self-affirmation"],
  "topic_audit": null
}

─── Example 3 (audit case) ────────────────────────────────────────────
Title: [hypothetical spoken-word piece on ecological mourning]
Artist: [hypothetical]
charge_summary: "Spoken meditation on ecological loss and species grief; not interpersonal grief."

Output:
{
  "deadpan_line": "Mourning the dying biosphere",
  "topics": [],
  "topic_audit": {
    "reason": "no match found",
    "proposed_tag": "ecological-grief",
    "rationale": "Existing 'grief' tag is interpersonal; 'political' is too narrow; 'existential' captures mortality but not species-scale ecological mourning."
  }
}
"""


def _build_system_prompt(taxonomy_text: str, count: int) -> str:
    return "\n".join([
        _VOICE_BLOCK,
        "═══════════════════════════════════════════════════════════════════════",
        f"TOPICS — closed taxonomy of {count} (do not invent)",
        "═══════════════════════════════════════════════════════════════════════",
        "",
        taxonomy_text,
        "",
        _TOPIC_RANKING_BLOCK,
        _AUDIT_BLOCK,
        _OUTPUT_BLOCK,
        _FEW_SHOT_BLOCK,
    ])


def _resolve_tagging_context() -> tuple[str, frozenset]:
    """(system_prompt, valid_slugs) for one tagging run.

    Resolves the taxonomy from its OWN short-lived session: tag_song runs in a
    worker thread (asyncio.to_thread), so it must never touch the request's
    Session. When the `taxonomy_db_driven.enabled` flag is on the definitions
    come from the DB (cached, TTL'd); otherwise from the code constants. Any
    failure falls back to the frozen code taxonomy so tagging never breaks.
    """
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            taxonomy_text = tax.taxonomy_for_prompt(db)
            slugs = tax.valid_slugs(db)
        finally:
            db.close()
    except Exception:
        logger.exception("ether_tagger taxonomy resolve failed; using code taxonomy")
        taxonomy_text, slugs = tax.taxonomy_for_prompt(), VALID_SLUGS
    return _build_system_prompt(taxonomy_text, len(slugs)), slugs


def _build_user_prompt(
    *,
    title: str,
    artist: str,
    lyrics: str,
    rubric_color: str | None,
    charge_value: int | None,
    charge_summary: str | None,
    listener_effects_prose: str | None,
) -> str:
    parts = [
        "Song to tag:",
        f"  Title:    {title}",
        f"  Artist:   {artist}",
        "",
        "Existing calibrator outputs (for grounding — do not repeat them):",
    ]
    if rubric_color:
        parts.append(f"  rubric_color:    {rubric_color}")
    if charge_value is not None:
        parts.append(f"  charge_value:    {charge_value:+d}")
    if charge_summary:
        parts.append(f"  charge_summary:  {charge_summary}")
    if listener_effects_prose:
        snippet = listener_effects_prose.strip().split("\n\n", 1)[0]
        parts.append(f"  listener_effects_prose:   {snippet}")

    trimmed = lyrics.strip()
    if len(trimmed) > 4000:
        trimmed = trimmed[:4000] + "\n... [truncated]"

    parts.extend([
        "",
        "Lyrics:",
        trimmed,
        "",
        "Tag it.",
    ])
    return "\n".join(parts)


def _parse_json_response(raw: str) -> dict | None:
    """Pull the JSON object out of the model's response. Returns None on parse failure."""
    raw = raw.strip()
    if not raw:
        return None

    brace_idx = raw.find("{")
    if brace_idx < 0:
        return None
    json_str = raw[brace_idx:]

    if json_str.rstrip().endswith("```"):
        json_str = json_str.rstrip()[:-3]
    json_str = json_str.strip()

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        last_brace = json_str.rfind("}")
        if last_brace > 0:
            try:
                return json.loads(json_str[:last_brace + 1])
            except json.JSONDecodeError:
                pass
        return None


def _truncate_to_word_boundary(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    last_space = cut.rfind(" ")
    if last_space > 0:
        cut = cut[:last_space]
    return cut.rstrip(",;:- ").strip()


def _sanitize(
    parsed: dict,
    *,
    title: str,
    artist: str,
    valid_slugs: frozenset = VALID_SLUGS,
) -> dict | None:
    """Apply the build pack §9 invariants. Returns the cleaned dict or None if
    the deadpan_line is missing entirely (caller decides whether to retry)."""
    deadpan = (parsed.get("deadpan_line") or "").strip().strip('"').strip()
    if not deadpan:
        return None

    # Spec cap: ~len(title)+len(artist), clamped by deadpan_guard (floor 45 so a
    # short title+artist cannot chop a legitimate placard, ceiling 60 so a long
    # featured-artist credit cannot license a sentence). The flat 80 that replaced
    # the old dynamic cap in 3499e8d was generous enough to let the tagger lane
    # creep toward sentence length; MAX_DEADPAN_LENGTH stays as the hard backstop.
    from app.services.agents.deadpan_guard import deadpan_length_cap
    cap = min(deadpan_length_cap(title, artist), MAX_DEADPAN_LENGTH)
    if len(deadpan) > cap:
        deadpan = _truncate_to_word_boundary(deadpan, cap)

    raw_topics = parsed.get("topics") or []
    if not isinstance(raw_topics, list):
        raw_topics = []

    valid_topics: list[str] = []
    invalid_dropped: list[str] = []
    for slug in raw_topics:
        if not isinstance(slug, str):
            continue
        slug = slug.strip()
        if slug in valid_slugs and slug not in valid_topics:
            valid_topics.append(slug)
        elif slug:
            invalid_dropped.append(slug)

    if len(valid_topics) > MAX_TOPICS:
        valid_topics = valid_topics[:MAX_TOPICS]

    audit = parsed.get("topic_audit")
    if not isinstance(audit, dict):
        audit = None

    # Enforce the contract: exactly one of (topics non-empty, audit non-null) is true.
    if valid_topics and audit:
        # Topics win — drop the contradictory audit.
        audit = None
    elif not valid_topics and not audit:
        # Neither branch satisfied. Synthesize an audit so the row surfaces for human eyes.
        audit = {
            "reason": "agent inconsistent output",
            "proposed_tag": invalid_dropped[0] if invalid_dropped else "",
            "rationale": (
                "Agent returned no taxonomy-valid topics and no audit payload."
                + (f" Invalid slug(s) dropped: {invalid_dropped}." if invalid_dropped else "")
            ),
        }

    return {
        "deadpan_line": deadpan,
        "topics": valid_topics,
        "topic_audit": audit,
    }


def _call_model(
    client: Anthropic,
    *,
    system_prompt: str,
    title: str,
    artist: str,
    lyrics: str,
    rubric_color: str | None,
    charge_value: int | None,
    charge_summary: str | None,
    listener_effects_prose: str | None,
) -> str | None:
    user_prompt = _build_user_prompt(
        title=title, artist=artist, lyrics=lyrics,
        rubric_color=rubric_color, charge_value=charge_value,
        charge_summary=charge_summary, listener_effects_prose=listener_effects_prose,
    )
    try:
        response = tracked_create(
            client,
            call_site="ether_tagger",
            context={"title": title, "artist": artist},
            model=AGENT_MODEL,
            max_tokens=512,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return (response.content[0].text or "").strip()
    except Exception:
        logger.exception("ether_tagger model call failed for %s / %s", title, artist)
        return None


def tag_song(
    *,
    title: str,
    artist: str,
    lyrics: str,
    rubric_color: str | None = None,
    charge_value: int | None = None,
    charge_summary: str | None = None,
    listener_effects_prose: str | None = None,
) -> Optional[dict]:
    """Run the ether tagger on a single song.

    Returns a dict {deadpan_line, topics, topic_audit} on success, or None
    on unrecoverable failure (caller leaves columns NULL).
    """
    if not title or not artist:
        return None
    if not lyrics or not lyrics.strip():
        logger.warning("ether_tagger called with empty lyrics for %s / %s — skipping", title, artist)
        return None

    client = Anthropic(api_key=settings.anthropic_api_key)

    # Resolve the taxonomy ONCE per song (DB-driven when the flag is on, else
    # code). Both attempts share the same prompt + valid-slug set.
    system_prompt, valid = _resolve_tagging_context()

    for attempt in (1, 2):
        raw = _call_model(
            client,
            system_prompt=system_prompt,
            title=title, artist=artist, lyrics=lyrics,
            rubric_color=rubric_color, charge_value=charge_value,
            charge_summary=charge_summary, listener_effects_prose=listener_effects_prose,
        )
        if raw is None:
            return None

        parsed = _parse_json_response(raw)
        if parsed is None:
            logger.warning(
                "ether_tagger attempt %d returned unparseable output for %s / %s: %s",
                attempt, title, artist, raw[:200],
            )
            continue

        cleaned = _sanitize(parsed, title=title, artist=artist, valid_slugs=valid)
        if cleaned is None:
            logger.warning(
                "ether_tagger attempt %d returned empty deadpan_line for %s / %s",
                attempt, title, artist,
            )
            continue

        return cleaned

    logger.error("ether_tagger exhausted retries for %s / %s — returning None", title, artist)
    return None
