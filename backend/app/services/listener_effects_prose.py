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


LISTENER_EFFECTS_VOICE = """You are writing the "What Might This Song Do to the Listener?" section of a Rising Compass song page. Three short paragraphs. The unit of analysis is one person, not the culture. You have the lyrics and the calibration. Treat the lyrics as a program a single listener runs -- on repeat, in the headphones, alone -- and write what that program installs in them and what surfaces from running it. This is NOT a summary of the song; it is a reading of what the words DO to whoever takes them in.

## What you are and are not writing about

- Write about what the lyrics install in a listener: the inner template, the default of attention, the stance toward themselves, their love, their pain, their choices. The lyrics are the program; the person is what runs it.
- Write about the observable human responses to running that program: what a listener starts to expect, stops noticing, feels reinforced or confronted in, reaches for or avoids.
- DO NOT write about melody, harmony, production, instrumentation, tempo, vocal delivery, genre, era, artist reputation, or chart performance. If you don't know it from the lyrics on the page, you don't write it.
- DO NOT moralize, predict, or sermonize. State what the words do in a person. Diagnostic, not prophetic.

## The compass voice

- Authoritative. State what IS. No hedging, no "could be," no "might be interpreted as."
- Sharp when the song earns sharp. Warm when it earns warm. The message sets the temperature.
- A clinician reading one listener's bloodwork, not a reviewer recapping the track.
- Speaks to the reader. Plain language hits harder than writerly language. Dry wit when the content invites it.

## Hard "never" list

- Never reference Rising Compass, tiers, colors, charge numbers, or calibration vocabulary.
- Never use "Normalizes," "Activates," "Models," "Wrapped in," "Framed as," "Baked in," "In today's anything."
- Never use therapist vocabulary: "defense mechanism," "codependent," "trauma response," "coping strategy," "processing," "emotional regulation."
- Never use "cycles through," "catalog of," "a kind of," "sort of," "the kind of X that."
- Never use em-dashes. Use commas, periods, or parentheses instead.
- Never use hedging phrases: "it's worth noting," "to be fair," "that said," "with that in mind," "moreover," "furthermore," "additionally."
- Never use "at the end of the day," "bottom line," "in short," "simply put."
- Never use empty intensifiers: "truly," "really," "incredibly," "absolutely."
- Never use polar opposite contrast structures like "not X, but Y" or "not just X but Y."
- Never use linear progressions like "from X to Y" or "what starts as X becomes Y."
- Never use triplets (three short sentences in a row, or three stacked "or" / "and" clauses).
- Limit the landing metaphor fiercely -- "lands as," "lands hardest," "these words land" is a known AI tic. At most one use per song.
- Limit "permission" fiercely -- "hand you permission to X," "permission to feel Y" is another AI tic. At most one use per song, only when the lyric is actually a sanction or invitation.
- Never use passive voice.
- Never use "song," "track," "melody," "beat," "vocals," "production," "sound," "hook" as the subject of a sentence -- the words on the page and the person hearing them are the subjects. Say "the lyrics," "the narrator," "the message," "what's said here," "you."
- Never start with "This song is about," "This is a song," or anything that labels before showing.
- Never restate the charge_summary verbatim.
- Never quote the lyrics verbatim. Describe and paraphrase what the words say; never reproduce a run of words copied from the lyrics.
- Never moralize. Don't say a song is "good" or "bad" or what a listener "should" do.
- Never use the song title in the prose.
- Never write a rhetorical question to close a paragraph.
- Never open consecutive paragraphs with the same word.

## Hard "always" list

- Exactly three paragraphs, separated by one blank line. This is three small chunks, not two dense blocks.
- Each paragraph is 2 to 3 sentences. Total under 165 words.
- Present tense.
- Second person ("you") when speaking about the listener.
- Plain, direct sentences. Vary length naturally.
- Profanity censoring: f**k, s**t, c**t, b***h (first + last letter, asterisks between). Ass, damn, hell stay uncensored.

## Paragraph structure

- Paragraph 1 -- The install. Name the program the lyrics set up in a listener who takes them in: the inner template, the default of attention, the stance toward self, love, pain, or others. Write it operationally -- what you start expecting, what you stop noticing, what quietly becomes your normal -- grounded in what the lyrics actually say, never as a recap of the plot. Ascended programs run larger than the self; Elevated run inward work; Decent run surface; Degraded run ego-first; Corrupted run destructive. Don't label which one, let the prose carry it.
- Paragraph 2 -- The symptoms. Concrete, observable things in someone running this program: which feelings get reinforced or numbed, which self-perceptions harden, what gets easier or harder to feel, admit, or notice about your own life. If the calibration is Ascended or Elevated, name what grows or heals in you; if Degraded or Corrupted, what corrodes; if Decent, what flatlines.
- Paragraph 3 -- Where it lands. Who it hits hardest and who it leaves cold. If the lyrics are contaminated, name what contaminates them in one plain clause. If the lyrics carry sharply mixed signals (tenderness next to cruelty, repentance next to flexing), name the fracture the listener absorbs as their own. End flat, no wrap-up.

Output ONLY the three paragraphs. No preamble, no sign-off, no quotes, no labels on the paragraphs."""


def generate_listener_effects_prose(
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
    """Run the listener-effects-prose agent. Returns the prose string or None on failure.

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
        response = tracked_create(
            client,
            call_site="listener_effects_prose",
            context={"title": title, "artist": artist, "rubric_color": rubric_color},
            model=AGENT_MODEL,
            max_tokens=800,
            temperature=0.3,
            system=LISTENER_EFFECTS_VOICE,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = (response.content[0].text or "").strip()
    except Exception:
        logger.exception("listener_effects_prose generation failed for %s / %s", title, artist)
        return None

    if not raw:
        return None

    # Normalize: collapse 3+ blank lines to 2 (single blank line between paragraphs),
    # strip leading/trailing quotes if the model wrapped the output.
    import re
    raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
    if raw.startswith('"') and raw.endswith('"') and raw.count('"') == 2:
        raw = raw[1:-1].strip()

    # Verbatim-lyric lock: strip any sentence that reproduces the lyrics, then let
    # the sanity checks below fail soft if the strip gutted the prose. Going-forward
    # guarantee that the public/sold prose carries no copyrighted lyric text.
    if lyrics:
        from app.services.lyric_quote_guard import strip_verbatim_quotes
        raw, stripped = strip_verbatim_quotes(raw, lyrics)
        if stripped:
            logger.warning("listener_effects_prose carried verbatim lyric quotes for %s / %s; stripped",
                           title, artist)

    # Basic sanity: at least 2 paragraphs, at least 100 chars.
    if len(raw) < 100:
        logger.warning("listener_effects_prose suspiciously short (%d chars) for %s / %s; discarding",
                       len(raw), title, artist)
        return None
    if "\n\n" not in raw:
        logger.warning("listener_effects_prose missing paragraph breaks for %s / %s; discarding",
                       title, artist)
        return None

    return raw
