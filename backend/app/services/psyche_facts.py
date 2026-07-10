"""Per-song Psyche Facts generator (the "Drug Facts" prescription bundle).

SYNTHESIS, not a fresh lyric read: the bundle is composed from the song's already-
generated fields (tier, charge, charge_summary, topics, deadpan_line, and the
listener/societal effects prose), never from the raw lyrics. Three payoffs: it is
cheap (a small prompt), fast, and LYRIC-FREE, so it can never carry a verbatim
lyric quote into a stored, public, sellable field (the verbatim-lyric guards do
not even need to touch it).

Returns the prescription dict (the sibling keys purpose, indicated_for[],
do_not_use_if, directions, onset, duration, warning) or None on any failure. Fails
soft: the caller stores NULL and the label degrades to the RC-derived layer.

Runs on Claude Opus on the PUBLIC path only (Lyrical Charger), gated by
`allow_prose_generation` in record_and_reconcile exactly like the two prose
generators. The terminal path (Claude Code is the model) supplies the bundle via
calibrate_song.py --psyche-facts-file and this generator never fires there. See
feedback_rc_no_api_in_terminal.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from anthropic import Anthropic

from app.config import settings
from app.services.claude_meter import tracked_create

logger = logging.getLogger(__name__)

AGENT_MODEL = settings.agent_model

TIER_LABELS = {
    "violet": "Ascended",
    "blue": "Elevated",
    "green": "Decent",
    "orange": "Degraded",
    "red": "Corrupted",
}

# The stored bundle's allowed keys (mirrors calibrate_song.py's allowlist and the
# chadlewine label_meta shape). The distilled effects[]/at_scale[] bullets are NOT
# keys -- the label renders the full listener/societal prose instead.
PF_STRING_KEYS = ("purpose", "do_not_use_if", "directions", "onset", "duration", "warning")
PF_ARRAY_KEYS = ("indicated_for",)


PSYCHE_FACTS_VOICE = """You are writing the "Psyche Facts" panel for a Rising Compass song page: a prescription label, in the exact register of an over-the-counter drug facts panel, for what one song does to the person who takes it. You are given the song's already-written clinical readings (its effect on a listener and on a society) plus its charge data. Compose the prescription from those, never from the raw song.

## Voice

A clinical pharmacist writing the label. Plain, exact, unsentimental, second person where the label speaks to the person taking it ("For the ...", "Play once when ...", "Do not use if ..."). No hype, no praise, no teardown. State what the song is for and what taking it does, at the same calm register a real drug-facts panel uses. Say nothing about melody, production, genre, era, or the artist.

## Output: a JSON object, these keys only

- "purpose": 1 to 2 sentences. What the song is FOR: the state it meets and what it does with it. Second person or impersonal.
- "indicated_for": an array of FOUR short noun phrases (the conditions/states it suits). Not sentences. Title-case-ish, e.g. "Anxious attachment".
- "do_not_use_if": 1 to 2 sentences. The genuine contraindication: the person or moment this song works against. Name the real risk, not a fake one.
- "directions": 1 to 2 sentences. How to take it, in dosing language ("Play once when ...", "Avoid looping it as ...").
- "onset": a short clinical phrase for how fast it takes hold ("Fast. Takes hold inside the first chorus.").
- "duration": a short clinical phrase for how long the effect lasts.
- "warning": 1 sentence. The caution a careful user should carry.

## Hard requirements

- Output ONLY the JSON object. No preamble, no code fences, no trailing prose.
- Every value is grounded in the supplied readings and charge. Do not invent effects the readings do not support.
- A positive or decent song gets its real, plain use stated. Do NOT manufacture a downside; do_not_use_if and warning still name a TRUE limit (e.g. a decent-but-static song can keep someone in a loop), never a teardown of a fine song.
- Never use em-dashes. Use commas, periods, or parentheses.
- Never reference Rising Compass, tiers, colors, charge numbers, or any calibration vocabulary.
- Never quote the song's lyrics.
- indicated_for is exactly four items.

## One example of the register (QUALITY BAR, do NOT copy its content)

{
  "purpose": "For the nervous, self-sabotaging pull toward someone you cannot act natural around. Makes that nervousness feel ordinary instead of shameful.",
  "indicated_for": ["Anxious attachment", "Crush nerves", "Caring too much and going quiet", "Self-reproach after losing your cool"],
  "do_not_use_if": "You are trying to talk yourself out of an attachment you have not actually let go of. The song sits inside the ambivalence instead of resolving it.",
  "directions": "Play once when the nerves hit, before you reach out. Avoid looping it as a stand-in for saying the thing out loud.",
  "onset": "Fast. Takes hold inside the first chorus.",
  "duration": "Runs the length of the track. The state it names outlasts the play only if the attachment does.",
  "warning": "Comfort with the nervous loop can turn into permission to stay in it."
}

Output the JSON object now."""


def _no_dash(s: str) -> str:
    """Belt-and-suspenders: the prompt bans em-dashes, but if one slips through,
    swap it for a comma and normalize the resulting spacing (no space before a
    comma, no doubled spaces)."""
    s = s.replace(" — ", ", ").replace("—", ", ").replace(" -- ", ", ")
    s = re.sub(r"\s+([,.])", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\s*,\s*$", "", s)  # no trailing comma left by an end-of-string dash
    return s.strip()


def _clean_bundle(obj: object) -> Optional[dict]:
    """Allowlist + trim + em-dash strip. Returns the cleaned bundle, or None when
    nothing usable survives (need at least `purpose`)."""
    if not isinstance(obj, dict):
        return None
    out: dict = {}
    for k, v in obj.items():
        if k in PF_STRING_KEYS and isinstance(v, str):
            t = _no_dash(v)
            if t:
                out[k] = t
        elif k in PF_ARRAY_KEYS and isinstance(v, list):
            arr = [_no_dash(x) for x in v if isinstance(x, str) and x.strip()]
            arr = [x for x in arr if x]
            if arr:
                out[k] = arr
    # A bundle with no purpose is not a usable label.
    if not out.get("purpose"):
        return None
    return out


def _parse_json_object(raw: str) -> Optional[dict]:
    """Best-effort decode of the model's JSON object (tolerates code fences and
    surrounding prose by extracting the first balanced {...} block)."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (ValueError, TypeError):
            return None
    return None


def generate_psyche_facts(
    *,
    title: str,
    artist: str,
    rubric_color: str,
    charge_value: int | None,
    charge_summary: str | None,
    contaminated: bool = False,
    contamination_note: str | None = None,
    deadpan_line: str | None = None,
    topics: str | list | None = None,
    listener_effects_prose: str | None = None,
    societal_effects_prose: str | None = None,
) -> Optional[dict]:
    """Synthesize the Psyche Facts prescription bundle. Returns the dict of allowed
    keys, or None on any failure (missing inputs, API error, unparseable output).

    Grounded in the listener/societal prose + charge; requires at least the
    listener prose and a charge_summary (the synthesis substrate). Fails soft.
    """
    if not rubric_color:
        return None
    # The bundle is a synthesis of the readings; without the listener prose and a
    # summary there is nothing to compose from, so skip (leave NULL).
    if not (listener_effects_prose and charge_summary):
        return None
    tier_label = TIER_LABELS.get(rubric_color, rubric_color)

    topics_str: str | None = None
    if topics:
        if isinstance(topics, str):
            try:
                parsed = json.loads(topics)
                topics_str = ", ".join(str(t) for t in parsed if t) if isinstance(parsed, list) else topics
            except (ValueError, TypeError):
                topics_str = topics
        elif isinstance(topics, list):
            topics_str = ", ".join(str(t) for t in topics if t)

    user_parts = [
        f'Song: "{title}" by {artist}',
        f"Tier: {tier_label}",
    ]
    if charge_value is not None:
        user_parts.append(f"Charge: {charge_value:+d}")
    if charge_summary:
        user_parts.append(f"Charge summary: {charge_summary}")
    if contaminated and contamination_note:
        user_parts.append(f"Contamination note: {contamination_note}")
    if deadpan_line:
        user_parts.append(f"Deadpan naming: {deadpan_line}")
    if topics_str:
        user_parts.append(f"Topics (dominant first): {topics_str}")
    user_parts.append(f"\nListener effects reading:\n{listener_effects_prose}")
    if societal_effects_prose:
        user_parts.append(f"\nSocietal effects reading:\n{societal_effects_prose}")
    user_parts.append("\nCompose the Psyche Facts JSON object now.")
    user_prompt = "\n".join(user_parts)

    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        response = tracked_create(
            client,
            call_site="psyche_facts",
            context={"title": title, "artist": artist, "rubric_color": rubric_color},
            model=AGENT_MODEL,
            max_tokens=900,
            temperature=0.6,
            system=PSYCHE_FACTS_VOICE,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = (response.content[0].text or "").strip()
    except Exception:
        logger.exception("psyche_facts generation failed for %s / %s", title, artist)
        return None

    if not raw:
        return None
    parsed = _parse_json_object(raw)
    bundle = _clean_bundle(parsed) if parsed is not None else None
    if bundle is None:
        logger.warning("psyche_facts output unparseable/empty for %s / %s", title, artist)
    return bundle
