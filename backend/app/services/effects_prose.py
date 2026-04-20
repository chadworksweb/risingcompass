"""Per-song effects prose generator.

Produces a 3-paragraph description of what a song transmits and what it
may do to a listener. Runs on Claude Opus alongside the calibrator so
the prose stays anchored to the canonical tier + charge_summary.

Replaces the tier-generic TIER_EFFECTS copy on the song page with
song-specific prose. Three short paragraphs:

  1. What the song transmits — the message, in clear terms, matching the
     tier.
  2. What that may do to a listener — phenomenological: awareness, mood,
     habit reinforcement. Not predictive; possibilities rooted in the
     messaging.
  3. Caveat / context — who might be affected differently, contamination
     flags if relevant, or how repeat listening compounds.

Paragraphs separated by blank lines. Frontend splits on \\n\\n and wraps
each in <p>. Fails soft — errors return None and the caller leaves the
column NULL; the public page falls back to the tier-generic copy.
"""

from __future__ import annotations

import logging
from typing import Optional

from anthropic import Anthropic

from app.config import settings

logger = logging.getLogger(__name__)

AGENT_MODEL = settings.agent_model

TIER_LABELS = {
    "violet": "Ascended",
    "blue": "Elevated",
    "green": "Decent",
    "orange": "Degraded",
    "red": "Corrupted",
}


EFFECTS_VOICE = """You are writing the "What Might This Song Do to the Listener?" section of a Rising Compass song page. Three short paragraphs — direct, clinical, humane.

## Hard constraints
- Exactly three paragraphs, separated by blank lines.
- Each paragraph 2–3 sentences max. Keep total under ~180 words.
- No headings, no bullets, no labels. Just the prose.
- Second person ("you", "the listener"). Present tense.
- Never moralize. State what the song transmits and what that does. No "good" / "bad" / "should".
- Never reference Rising Compass, tiers, colors, or charge numbers. The reader has no context for those labels.
- Never restate the charge_summary verbatim — you've been given it to anchor the reading, not to repeat.
- Never use: "Normalizes", "Activates", "Models", "Wrapped in", "Framed as", "Baked in", "Journey", "Playlist".
- Never use passive voice.
- Never start with "This song is about..." — the reader already knows the title.
- Profanity: censor f**k, s**t, c**t, b***h (first + last letter, asterisks). Ass/damn/hell uncensored.
- Em-dashes: use 1 out of every 10 times you want to.

## Structure
- ¶1: what the song transmits. The message on the page, in clear terms. Grounded in the dominant arc, not individual lines. Matches the tier without naming it — Ascended songs should read expansive, Elevated as inward work, Decent as surface, Degraded as ego-first, Corrupted as destructive.
- ¶2: what that may do to a listener. Phenomenological — what it activates, reinforces, avoids, or teaches on repeat. Possibilities, not prophecy. No "this will make you..." absolutes; instead "repeated listening can..." / "the song offers..." / "you may find...".
- ¶3: caveat or context. Who the song hits hardest, who it might leave cold, contamination flags if relevant, or how repeat listening compounds or wears out. Name the condition under which the effect holds. If contaminated, acknowledge the specific contamination plainly (one short clause) so the reader knows it's there without scolding.

Output ONLY the three paragraphs. Nothing else — no preamble, no sign-off, no quote marks."""


def generate_effects_prose(
    *,
    title: str,
    artist: str,
    rubric_color: str,
    charge_value: int | None,
    charge_summary: str | None,
    contaminated: bool = False,
    contamination_note: str | None = None,
    lyrics: str | None = None,
) -> Optional[str]:
    """Run the effects-prose agent. Returns the prose string or None on failure.

    Fails soft — the caller stores NULL on None so the page falls back to
    the tier-generic copy.
    """
    if not rubric_color:
        return None
    tier_label = TIER_LABELS.get(rubric_color, rubric_color)

    user_parts = [
        f'Song: "{title}" by {artist}',
        f"Tier: {tier_label}",
    ]
    if charge_value is not None:
        user_parts.append(f"Charge: {charge_value:+d}")
    if charge_summary:
        user_parts.append(f"Calibration anchor (do not repeat verbatim): {charge_summary}")
    if contaminated and contamination_note:
        user_parts.append(f"Contamination note: {contamination_note}")
    if lyrics:
        # Truncate lyrics — the prose doesn't need a full pass, just a reference.
        trimmed = lyrics.strip()
        if len(trimmed) > 4000:
            trimmed = trimmed[:4000] + "\n... [truncated]"
        user_parts.append(f"\nLyrics:\n{trimmed}")
    user_parts.append("\nWrite the three paragraphs now.")

    user_prompt = "\n".join(user_parts)

    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=AGENT_MODEL,
            max_tokens=800,
            temperature=0.3,
            system=EFFECTS_VOICE,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = (response.content[0].text or "").strip()
    except Exception:
        logger.exception("effects_prose generation failed for %s / %s", title, artist)
        return None

    if not raw:
        return None

    # Normalize: collapse 3+ blank lines to 2 (single blank line between paragraphs),
    # strip leading/trailing quotes if the model wrapped the output.
    import re
    raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
    if raw.startswith('"') and raw.endswith('"') and raw.count('"') == 2:
        raw = raw[1:-1].strip()

    # Basic sanity: at least 2 paragraphs, at least 100 chars.
    if len(raw) < 100:
        logger.warning("effects_prose suspiciously short (%d chars) for %s / %s; discarding",
                       len(raw), title, artist)
        return None
    if "\n\n" not in raw:
        logger.warning("effects_prose missing paragraph breaks for %s / %s; discarding",
                       title, artist)
        return None

    return raw
