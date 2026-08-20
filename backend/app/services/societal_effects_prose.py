"""Per-song societal effects prose generator.

Produces a 2-paragraph reading of what a society running this song's program at
scale would manifest. Parallel to listener_effects_prose (same two-paragraph
contract + the same in-code word cap), but the unit of analysis is the
collective, not the individual listener.

Grounded in lyrics + calibration + the Ether Art Chart fields (deadpan_line +
topics) when available, so the prose can speak directly to "if millions
manifest these topics/feelings/experiences, here is what emerges socially and
psychologically." Topics may be NULL on songs that pre-date the ether tagger
or rows still queued for backfill -- the prompt gracefully degrades to the
calibration + lyrics anchor.

The reading stays on the social EFFECT, not on which subpopulations absorb it,
and names the song title once (for search visibility). Fails soft. On error
returns None and the caller leaves the column NULL; the public song page hides
the section entirely (no tier-generic fallback).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from anthropic import Anthropic

from app.config import settings
from app.services.claude_meter import tracked_create
from app.services.prose_format import trim_to_word_cap

logger = logging.getLogger(__name__)

AGENT_MODEL = settings.agent_model

# Hard ceiling on the prose length (slightly higher than listener -- the
# society scale carries a touch more). Enforced by a sentence-boundary trim.
SOCIETAL_MAX_WORDS = 130


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


SOCIETAL_VOICE = """You are writing the "What Might This Song Do to a Society?" section of a Rising Compass song page. Two short paragraphs. The unit of analysis is the collective: a whole society that listens to and sings along with this song. This is NOT a summary of the song, and NOT a reading of one listener. It is a reading of what the words DO to a society at scale.

## Voice

A senior clinician writing the discussion section of a paper on this song's population-level effects. The register is academic and diagnostic: formal, precise, measured, and unsentimental, stating findings about a population as a specialist would state them in a medical journal. Authoritative and exact. Clinical and analytical vocabulary is preferred, and nominalized constructions (a decline in, a normalization of, the aggregate effect) are welcome where they add precision. Do not editorialize, moralize, or predict apocalypse; report what manifests in a population. Say nothing about melody, production, genre, era, or the artist's reputation. Avoid pop-sociology slang (no "parasocial," "atomization," "cultivation," "cognitive dissonance," "anomie," "hegemony"); describe the same phenomena in plain clinical terms.

## Society is the actor; culture is the product

The society (the people) is the subject; culture is what that listening creates, downstream, an output, never the thing doing the listening. The subject is a society, a population, an institution, or people. A society "incorporates," "absorbs," "circulates," "sings along with," or "rehearses" the song. It does NOT "run" the song (that is one person, the listener lane). "Culture" appears only as the created result, if at all, never as the listener.

## The two paragraphs, sentence by sentence

Give each sentence a distinct job so no two restate each other, and vary their length hard: at least one short, blunt sentence per paragraph.

- Paragraph 1, the pattern at scale.
  - THESIS: the population-level pattern the song installs when a whole society takes it up, with the exact song title in it. State it precisely.
  - MECHANISM: how it plays out at scale, drawn from what the lyrics say. If dominant topics are supplied, integrate them by what they DO socially, never as a list. Report the process, not a vivid flourish.
  - NEW NORMAL: the civic, relational, or communicative default it leaves behind. Often short.
- Paragraph 2, the symptoms.
  - PRIMARY SYMPTOM: the main effect that emerges in a population that takes this up. If the charge is positive, name what develops; if negative, what degrades.
  - SECOND-ORDER SYMPTOM: what that first symptom then produces, which capacities atrophy, which trust erodes.
  - LANDING: a precise final clinical finding.
- Do NOT insert a manufactured downside or hedge a good effect. A merely-decent song still gets its real social effect stated plainly; the "what flatlines / it never adds up to" teardown is banned unless the song genuinely corrupts. End flat, no wrap-up.

## How this voice builds a sentence

- The society or the people are the subject that changes: "a society learns," "people demand," "communities stay intact." Never the song, the words, the narrator, or the artist as the actor in the effect. The performer is irrelevant; write only what happens in the population.
- Vary the subject and the opening of every sentence. Do not run a fixed frame, and do not open consecutive paragraphs with the same word. Do not open with "Run at scale," "At scale," "A population absorbing," or "When a population runs it."
- State verdicts as plain sentences, never as "What flatlines is..." or "What it does not do is..." constructions.
- State claims positively. Do not define by the foil: no "X, not Y," no "instead of," no "rather than," no "less like X, more like Y," no "not just X but Y."
- NEVER SAY WHAT SOMETHING IS NOT. The foil list above is the loud form; the quiet forms are what actually get through, and they are equally banned: "says nothing about it," "silent about himself," "asks nothing back," "legible to no one," "less practised at asking," "unspoken," "unaware," "cannot see it." Each reads as description and is a hole where the description should be. Name the state that IS present instead, in the individual or the population: what they keep, do, doubt, expect, or turn toward. Someone who says nothing about his own wanting keeps it to himself; a want nobody states stays private; a threshold does not fail to rise, it rises.
- Name social processes precisely, in clinical terms. Do not render feeling as a physical object (hold, carry, set down, weight, under a loss) or as sizes and volumes.
- No triplets (three short sentences in a row, or three stacked "or" / "and" clauses). Make no point twice; redundancy is the main thing to cut.

## Two examples of the voice (these are the QUALITY BAR, not just clean samples; match their register, precision, and sentence-building. Clean is only the floor. Do NOT copy their content.)

Example A, a decent / mildly affirming song:

A society that incorporates Turn The Mill into its common repertoire grows less tolerant of arrangements that run on human depletion. Sustained collective rehearsal of the song's refusal reclassifies self-sacrifice, once valorized as loyalty, as a burden the individual may legitimately decline, and the accepted threshold for exit falls. Withdrawal acquires social legitimacy.

At the population level the pattern manifests as a contraction in the supply of self-effacing labor: institutions reliant on chronic overextension experience attrition among those previously depended upon to absorb the shortfall. Cessation comes to signify fortitude, and the stigma once attached to quitting diminishes. The aggregate effect is a measurable hardening of collective boundaries.

Example B, a corrupted, harmful song:

A society that absorbs Kill You into common circulation exhibits a progressive normalization of graphic violence against women. Repeated communal performance reconstitutes depictions of rape and homicide as entertainment, and a competitive escalation follows, with reach accruing to whichever iteration is most extreme. Objection functions as amplification; it extends the material's reach.

At the population level, the threshold for perceiving the brutalization of women as serious rises measurably. Cruelty toward women migrates into ordinary public speech, and the vocabulary of real violence loses its gravity through repetition as chorus. Women occupy a public sphere in which representations of their own destruction circulate as shared recreation.

## Hard requirements

- Exactly two paragraphs, one blank line between. Each 2 to 3 tight sentences. Under 130 words total. Shorter and sharper beats longer.
- Name the exact song title once, in paragraph 1, and never again.
- Present tense. Third person ("a society," "people," "a population"). Do NOT use "you"; this section describes the collective, not the individual reader.
- Never reference Rising Compass, tiers, colors, charge numbers, or any calibration vocabulary.
- Never restate the charge_summary verbatim.
- Never use em-dashes. Use commas, periods, or parentheses.
- Never quote the lyrics verbatim. Describe and paraphrase what the words say.
- Profanity censoring: f**k, s**t, c**t, b***h (first and last letter, asterisks between). Ass, damn, hell stay uncensored.

Output ONLY the two paragraphs. No preamble, no labels, no surrounding quotes."""


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
    listener_effects_prose: str | None = None,
) -> Optional[SocietalProseResult]:
    """Run the societal-effects agent. Returns a SocietalProseResult (prose +
    sealed generation provenance) or None on failure.

    `topics` may be a JSON-encoded string (the column format) or a list. Both
    are normalized to a comma-separated string before being passed to the
    model. Missing topics are tolerated -- the prompt degrades to lyrics +
    calibration anchor.

    Fails soft -- on None (call error, empty / too-short / malformed output),
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
    if listener_effects_prose:
        user_parts.append(
            "Per-listener effects prose (for reference, do not repeat -- your job is the "
            f"society scale):\n{listener_effects_prose}"
        )
    if lyrics:
        trimmed = lyrics.strip()
        if len(trimmed) > 4000:
            trimmed = trimmed[:4000] + "\n... [truncated]"
        user_parts.append(f"\nLyrics:\n{trimmed}")
    user_parts.append("\nWrite the two paragraphs now.")

    user_prompt = "\n".join(user_parts)

    # Rule-O gate: teardown language is only legitimate on a genuinely negative
    # song. orange/red -> allow it; everything else is held to "report the real
    # effect, no manufactured downside."
    allow_deficit = rubric_color in ("orange", "red")

    from app.services.prose_tell_guard import hard_findings
    from app.services.prose_judge import judge as _judge

    def _issues(p: str) -> list:
        """Regex hard findings + semantic-judge findings; all treated hard. The
        judge is fail-soft (a broken judge adds nothing; the regex floor stands)."""
        reg = hard_findings(p, lane="societal", allow_deficit=allow_deficit)
        try:
            sem = _judge(p, lane="societal", positive=not allow_deficit)
        except Exception:
            logger.exception("prose_judge failed (non-fatal) for %s / %s", title, artist)
            sem = []
        return list(reg) + list(sem)

    def _isum(issues) -> str:
        return ", ".join(getattr(f, "code", "?") for f in issues) or "clean"

    def _clean(raw: str) -> Optional[str]:
        """Normalize + lyric-guard + sanity + word-cap one raw output. None on fail."""
        import re
        raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
        if raw.startswith('"') and raw.endswith('"') and raw.count('"') == 2:
            raw = raw[1:-1].strip()
        if lyrics:
            from app.services.lyric_quote_guard import strip_verbatim_quotes
            raw, stripped = strip_verbatim_quotes(raw, lyrics, title=title)
            if stripped:
                logger.warning("societal_effects_prose carried verbatim lyric quotes for %s / %s; stripped",
                               title, artist)
        if len(raw) < 120 or "\n\n" not in raw:
            logger.warning("societal_effects_prose failed sanity (len/paragraphs) for %s / %s", title, artist)
            return None
        return trim_to_word_cap(raw, SOCIETAL_MAX_WORDS)

    def _attempt(system: str):
        """One model call + clean. Returns (prose, model, generated_at) or None."""
        try:
            client = Anthropic(api_key=settings.anthropic_api_key)
            response = tracked_create(
                client,
                call_site="societal_effects_prose",
                context={"title": title, "artist": artist, "rubric_color": rubric_color},
                model=AGENT_MODEL,
                max_tokens=900,
                # Retune: exemplars carry the control the ban-wall used to, so
                # temperature comes up off 0.3 for burstiness / less default voice.
                temperature=0.7,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
            )
            # Seal provenance at the moment the call succeeds, before any
            # post-processing -- the timestamp the prophecy instrument proves against.
            generated_at = datetime.now(timezone.utc)
            model = getattr(response, "model", None) or AGENT_MODEL
            raw = (response.content[0].text or "").strip()
        except Exception:
            logger.exception("societal_effects_prose generation failed for %s / %s", title, artist)
            return None
        if not raw:
            return None
        prose = _clean(raw)
        return (prose, model, generated_at) if prose is not None else None

    # Attempt 1.
    result = _attempt(SOCIETAL_VOICE)
    if result is None:
        return None
    prose, model, generated_at = result
    issues = _issues(prose)

    # Fail-closed regen (see listener_effects_prose for the rationale): regex floor
    # + semantic judge. Regenerate once with the findings, keep whichever is
    # cleaner; if issues survive, return None so the page hides the section rather
    # than ship slop unread.
    if issues:
        logger.info("societal_effects_prose attempt 1 tripped the guard for %s / %s [%s]; regenerating",
                    title, artist, _isum(issues))
        correction = (
            "\n\n## Your previous attempt tripped the voice guard\n"
            "Rewrite the two paragraphs clean, fixing every issue below and keeping "
            "everything that was already working:\n"
            + "\n".join(f'- {f.name}: found "{f.snippet}"' for f in issues)
        )
        result2 = _attempt(SOCIETAL_VOICE + correction)
        if result2 is not None:
            prose2, model2, generated_at2 = result2
            issues2 = _issues(prose2)
            if len(issues2) < len(issues):
                prose, model, generated_at, issues = prose2, model2, generated_at2, issues2

    if issues:
        logger.warning(
            "societal_effects_prose FAIL-CLOSED (NULL) for %s / %s: %d issue(s) survive regen [%s]",
            title, artist, len(issues), _isum(issues),
        )
        return None

    # SEO: the prose should name the song once. Soft check -- log, never fail.
    if title and title.lower() not in prose.lower():
        logger.info("societal_effects_prose did not include the title for %s / %s", title, artist)

    return SocietalProseResult(prose=prose, model=model, generated_at=generated_at)
