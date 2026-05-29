"""Per-song societal effects prose generator.

Produces a 2-paragraph (optionally 3) description of what a society running
this song's program at scale would manifest. Parallel to effects_prose, but
the unit of analysis is the collective, not the individual listener.

Grounded in lyrics + calibration + the Ether Art Chart fields (deadpan_line +
topics) when available, so the prose can speak directly to "if millions
manifest these topics/feelings/experiences, here is what emerges socially and
psychologically." Topics may be NULL on songs that pre-date the ether tagger
or rows still queued for backfill — the prompt gracefully degrades to the
calibration + lyrics anchor.

Fails soft. On error returns None and the caller leaves the column NULL; the
public song page hides the section entirely (no tier-generic fallback).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from anthropic import Anthropic

from app.config import settings
from app.services.claude_meter import tracked_create

logger = logging.getLogger(__name__)

AGENT_MODEL = settings.agent_model


@dataclass
class SocietalProseResult:
    """Sealed generation provenance for one societal-effects prose value.

    `generated_at` is captured at the moment the Anthropic call succeeds (NOT at
    row insert), and `model` is the model that produced the prose. Callers
    persist all three together so provenance stays in lockstep with the prose.
    """

    prose: str
    model: str
    generated_at: datetime

TIER_LABELS = {
    "violet": "Ascended",
    "blue": "Elevated",
    "green": "Decent",
    "orange": "Degraded",
    "red": "Corrupted",
}


SOCIETAL_VOICE = """You are writing the "What Might This Song Do to a Society?" section of a Rising Compass song page. Two short paragraphs (a third is permitted only if the song's lyrics carry sharply mixed signals — see ¶3 rule). The unit of analysis is the collective, not the individual listener. You have the lyrics, the calibration, and (sometimes) the Ether Art Chart fields: a deadpan literal naming of the song and its dominant topic tags. Treat the song as a piece of mass mental programming that millions ingest daily, and write what shows up in a population running that program.

## What you are and are not writing about

- Write about what happens at scale: communication patterns, relational templates, civic baselines, attentional defaults, what conversations become possible or impossible, what conflicts become routine, what trust gets eroded or reinforced, what kinds of grievance or longing get magnified into a culture's air.
- The lyrics are the program. The topics are the surface area of that program in the culture. Speak to both.
- DO NOT write about melody, harmony, production, instrumentation, tempo, vocal delivery, genre, era, artist reputation, chart performance.
- DO NOT moralize, predict apocalypse, or sermonize. State what symptoms emerge in a population running this program. Diagnostic, not prophetic.
- Sociology and psychology in plain language. NEVER name theorists, schools, or technical jargon ("anomie," "cultivation theory," "mimetic," "parasocial," "cognitive dissonance," "attachment style"). Carry the ideas, not the labels.

## The compass voice

- Authoritative. State what IS. No hedging, no "could be," no "it might be the case that."
- Sharp when the song earns sharp. Warm when it earns warm. The message sets the temperature.
- Speaks to the reader. Plain language hits harder than writerly language.
- Diagnostic clarity. The compass is a clinician describing the bloodwork of a culture, not a pundit shouting predictions.

## Hard "never" list

- Never reference Rising Compass, tiers, colors, charge numbers, or calibration vocabulary.
- Never use "Normalizes," "Activates," "Models," "Wrapped in," "Framed as," "Baked in," "In today's anything."
- Never use therapist or academic vocabulary: "defense mechanism," "codependent," "trauma response," "coping strategy," "processing," "emotional regulation," "anomie," "atomization," "parasocial," "cultivation," "mimetic," "cognitive dissonance," "in-group," "out-group," "social proof," "discourse," "hegemony."
- Never use "cycles through," "catalog of," "a kind of," "sort of," "the kind of X that."
- Never use em-dashes. Use commas, periods, or parentheses instead.
- Never use hedging phrases: "it's worth noting," "to be fair," "that said," "with that in mind," "moreover," "furthermore," "additionally."
- Never use "at the end of the day," "bottom line," "in short," "simply put."
- Never use empty intensifiers: "truly," "really," "incredibly," "absolutely."
- Never use polar opposite contrast structures like "not X, but Y" or "not just X but Y."
- Never use linear progressions like "from X to Y" or "what starts as X becomes Y."
- Never use triplets (three short sentences in a row, or three stacked "or" / "and" clauses).
- Never use "lands as," "lands hardest," "permission to feel X," "give you permission" (AI tics). Use them at most once per song, only when no other verb carries the meaning.
- Never use passive voice.
- Never use the song title in the prose.
- Never write a rhetorical question to close a paragraph.
- Never open consecutive paragraphs with the same word.
- Never restate the charge_summary verbatim.
- Never quote the lyrics verbatim. Describe and paraphrase what the words say; never reproduce a run of words copied from the lyrics. The lyrics are your source, never your text.
- Never moralize or prescribe ("a society should," "people need to," "we have to").

## Hard "always" list

- Two paragraphs minimum, three maximum (third only when ¶3 rule fires).
- One blank line between paragraphs.
- Each paragraph 2 to 4 sentences. Total under 200 words.
- Present tense.
- Third person plural ("a population," "people," "a culture") when speaking about the society. Do NOT use "you" — this section is not addressing the individual reader, it is describing the collective.
- Plain, direct sentences. Vary length naturally.
- Profanity censoring: f**k, s**t, c**t, b***h. Ass, damn, hell stay uncensored.

## Paragraph structure

- ¶1: Name the program at scale. What pattern of attention, what relational template, what civic or communicative default does this song install when millions run it daily? Write the program in operational terms — what people start expecting, what they stop noticing, what becomes the new normal in how they talk, partner, work, grieve, or organize. Ground in what the lyrics actually say. If topics are supplied, weave them in by what they DO socially, not by listing them.
- ¶2: The symptoms that emerge. Concrete, observable things in a population running this program: which conversations become harder, which conflicts become routine, which kinds of trust erode, which kinds of longing or grievance get magnified, which capacities atrophy, which compensatory behaviors compound. Diagnostic. End flat, no wrap-up. If the calibration is Ascended or Elevated, the symptoms section describes what flourishes, not what rots. If Degraded or Corrupted, describe what rots. If Decent, describe what flatlines.
- ¶3 (CONDITIONAL — only fire when the lyrics carry sharply mixed signals: tender devotion next to crass objectification, sincere repentance next to flexing, communion language next to contempt, etc.): Name the incoherence in plain language and what it produces at scale. The songwriter could not hold a single posture for three minutes. The listener absorbs that fracture. A culture absorbing fractured programs at scale produces populations who cannot hold a single posture in their own lives, conversations, or commitments. Keep this paragraph to 2 or 3 sentences. Do NOT fire ¶3 just because a song has emotional range; fire it only when the postures are in actual moral tension with each other and the song never resolves them.

Output ONLY the paragraphs. No preamble, no sign-off, no quotes, no labels on the paragraphs."""


def generate_societal_effects_prose(
    *,
    title: str,
    artist: str,
    rubric_color: str,
    charge_value: int | None,
    charge_summary: str | None,
    contaminated: bool = False,
    contamination_note: str | None = None,
    lyrics: str | None = None,
    deadpan_line: str | None = None,
    topics: str | list | None = None,
    effects_prose: str | None = None,
) -> Optional[SocietalProseResult]:
    """Run the societal-effects agent. Returns a SocietalProseResult (prose +
    sealed generation provenance) or None on failure.

    `topics` may be a JSON-encoded string (the column format) or a list. Both
    are normalized to a comma-separated string before being passed to the
    model. Missing topics are tolerated — the prompt degrades to lyrics +
    calibration anchor.

    Fails soft — on None (call error, empty / too-short / malformed output),
    the result carries NO metadata and the caller leaves all three columns
    (prose + generated_at + model) untouched, so the public page hides the
    section.
    """
    if not rubric_color:
        return None
    tier_label = TIER_LABELS.get(rubric_color, rubric_color)

    topics_str: str | None = None
    if topics:
        if isinstance(topics, str):
            try:
                parsed = json.loads(topics)
                if isinstance(parsed, list):
                    topics_str = ", ".join(str(t) for t in parsed if t)
                else:
                    topics_str = topics
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
        user_parts.append(f"Calibration anchor (do not repeat verbatim): {charge_summary}")
    if contaminated and contamination_note:
        user_parts.append(f"Contamination note: {contamination_note}")
    if deadpan_line:
        user_parts.append(f"Deadpan naming (Ether Art Chart): {deadpan_line}")
    if topics_str:
        user_parts.append(f"Dominant topics (Ether Art Chart, dominant first): {topics_str}")
    if effects_prose:
        user_parts.append(
            "Per-listener effects prose (for reference, do not repeat — your job is the "
            f"society scale):\n{effects_prose}"
        )
    if lyrics:
        trimmed = lyrics.strip()
        if len(trimmed) > 4000:
            trimmed = trimmed[:4000] + "\n... [truncated]"
        user_parts.append(f"\nLyrics:\n{trimmed}")
    user_parts.append("\nWrite the paragraphs now.")

    user_prompt = "\n".join(user_parts)

    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        response = tracked_create(
            client,
            call_site="societal_effects_prose",
            context={"title": title, "artist": artist, "rubric_color": rubric_color},
            model=AGENT_MODEL,
            max_tokens=900,
            temperature=0.3,
            system=SOCIETAL_VOICE,
            messages=[{"role": "user", "content": user_prompt}],
        )
        # Seal provenance at the moment the call succeeds, before any
        # post-processing -- this is the timestamp the prophecy instrument
        # proves against, not the eventual row insert.
        generated_at = datetime.utcnow()
        model = getattr(response, "model", None) or AGENT_MODEL
        raw = (response.content[0].text or "").strip()
    except Exception:
        logger.exception(
            "societal_effects_prose generation failed for %s / %s", title, artist
        )
        return None

    if not raw:
        return None

    import re
    raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
    if raw.startswith('"') and raw.endswith('"') and raw.count('"') == 2:
        raw = raw[1:-1].strip()

    # Verbatim-lyric lock: strip any sentence that reproduces the lyrics; the
    # checks below fail soft if the strip gutted it. No copyrighted lyric text
    # ships in the public/sold prose.
    if lyrics:
        from app.services.lyric_quote_guard import strip_verbatim_quotes
        raw, stripped = strip_verbatim_quotes(raw, lyrics)
        if stripped:
            logger.warning("societal_effects_prose carried verbatim lyric quotes for %s / %s; stripped",
                           title, artist)

    if len(raw) < 120:
        logger.warning(
            "societal_effects_prose suspiciously short (%d chars) for %s / %s; discarding",
            len(raw), title, artist,
        )
        return None
    if "\n\n" not in raw:
        logger.warning(
            "societal_effects_prose missing paragraph breaks for %s / %s; discarding",
            title, artist,
        )
        return None

    return SocietalProseResult(prose=raw, model=model, generated_at=generated_at)
