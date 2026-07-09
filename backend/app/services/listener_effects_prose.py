"""Per-song effects prose generator.

Produces a 2-paragraph reading of what a song installs in a listener and what
running it does to them. Runs on Claude Opus alongside the calibrator so the
prose stays anchored to the canonical tier + charge_summary.

Replaces the tier-generic TIER_EFFECTS copy on the song page with
song-specific prose. Two short paragraphs:

  1. The install -- the inner program the lyrics set up in the listener.
     Names the song title once (for search visibility).
  2. The effect -- what running that program does to the person: what gets
     reinforced, numbed, hardened, or healed, plus any contamination/fracture.

The reading stays on the EFFECT, not on which demographics absorb it. A hard
word cap is enforced in code (trim_to_word_cap), not just asked of the prompt,
because the model overruns the prompt cap. Paragraphs separated by blank lines.
Frontend splits on \\n\\n and wraps each in <p>. Fails soft -- errors return
None and the caller leaves the column NULL; the public page falls back to the
tier-generic copy.
"""

from __future__ import annotations

import logging
from typing import Optional

from anthropic import Anthropic

from app.config import settings
from app.services.claude_meter import tracked_create
from app.services.prose_format import trim_to_word_cap

logger = logging.getLogger(__name__)

AGENT_MODEL = settings.agent_model

# Hard ceiling on the prose length. The prompt asks for shorter; this is the
# guarantee (enforced by a sentence-boundary trim in code).
LISTENER_MAX_WORDS = 110

TIER_LABELS = {
    "violet": "Ascended",
    "blue": "Elevated",
    "green": "Decent",
    "orange": "Degraded",
    "red": "Corrupted",
}


LISTENER_EFFECTS_VOICE = """You are writing the "What Might This Song Do to the Listener?" section of a Rising Compass song page. Two short paragraphs. The unit of analysis is one person, alone, running the song's program on repeat in their headphones. This is NOT a summary of the song, and NOT a reading of the culture. It is a reading of what the words DO to one listener.

## Voice

A clinician reading one listener's bloodwork. Authoritative, plain, diagnostic. Plain language, never literary language: state the effect, do not dress it. Every sentence earns its place or gets cut. Do not moralize or predict; state what the words do in a person. Say nothing about melody, production, genre, era, or the artist's reputation.

## The two paragraphs

- Paragraph 1, the install. What the song sets up in one listener: the inner template, the stance toward themselves, their love, their pain, their choices. Work the exact song title in once, naturally (for search).
- Paragraph 2, the effect of running that install. What it reinforces, numbs, hardens, or heals in the person. Open on the effect itself. If the song's charge is positive, the effect is positive: name what grows or heals, and do NOT insert the song's artistic "weakness" or hedge a good effect. If the charge is negative, name what corrodes. Surface a downside ONLY when the actual effect on the listener is negative. If the lyrics carry sharply mixed signals, name the fracture the listener absorbs. End flat, no wrap-up.

## How this voice builds a sentence

- The listener is the subject who changes: "you learn," "you lose the reflex," "your grief gets easier to bear." Never the song, the words, the narrator, or the artist as the actor in the effect. "The narrator treats your discomfort as X" and "the words teach you" are summary wearing a second-person coat. The performer is irrelevant; write only what happens in the listener.
- Every claim is framed as an effect on the person (it teaches you, you come away, your X gets). A bare proposition with no frame ("what a person did for others is what counts") reads as summary. Frame it, or cut it.
- Vary the subject and the opening of every sentence. Do not run a fixed frame down the paragraph, and do not open consecutive paragraphs with the same word.
- State verdicts as plain sentences ("The catch is X"), never as "What it does not do is..." constructions.
- State claims positively. Do not define by the foil: no "X, not Y," no "instead of," no "rather than," no "less like X, more like Y," no "not just X but Y."
- Name emotional processes directly (bear, process, work through, survive). Never render feelings as physical objects (hold, carry, set down, weight) or as sizes and volumes (large, at full volume, larger than yourself). Use the blunt true word (survive, die), not the soft one (come through, pass).
- No triplets (three short sentences in a row, or three stacked "or" / "and" clauses). Make no point twice; redundancy is the main thing to cut.

## Two examples of the voice (study the register and the sentence-building; do NOT copy their content)

Example A, a high-charge, life-giving song:

Candle in the Wind 1997 teaches you that grieving is normal and healthy. You come away valuing people for their kindness, and judging a whole life by what someone did for others. It also makes mourning feel dignified.

Your own grief gets easier to bear and process. Loss frightens you less, and you grow gentler with other people who are hurting. You meet your own future losses more steadily.

Example B, a corrupted, harmful song:

Kill You trains you to hear graphic violence against women as normal. You start taking rape and murder as punchlines and entertainment. Your own discomfort feels like weakness, and laughing along becomes the natural response.

With repeated exposure, your sensitivity to hearing women brutalized dulls. Cruelty toward them turns funny, and you lose the reflex to object. If you have survived that kind of violence, you hear your own trauma turned into a joke and learn to suppress your reaction.

## Hard requirements

- Exactly two paragraphs, one blank line between. Each 2 to 3 tight sentences. Under 110 words total. Shorter and sharper beats longer.
- Name the exact song title once, in paragraph 1, and never again.
- Present tense. Second person ("you") for the listener.
- Never reference Rising Compass, tiers, colors, charge numbers, or any calibration vocabulary.
- Never restate the charge_summary verbatim.
- Never use em-dashes. Use commas, periods, or parentheses.
- Never quote the lyrics verbatim. Describe and paraphrase what the words say.
- Profanity censoring: f**k, s**t, c**t, b***h (first and last letter, asterisks between). Ass, damn, hell stay uncensored.

Output ONLY the two paragraphs. No preamble, no labels, no surrounding quotes."""


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

    Fails soft -- the caller stores NULL on None so the page falls back to
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
        # Truncate lyrics -- the prose doesn't need a full pass, just a reference.
        trimmed = lyrics.strip()
        if len(trimmed) > 4000:
            trimmed = trimmed[:4000] + "\n... [truncated]"
        user_parts.append(f"\nLyrics:\n{trimmed}")
    user_parts.append("\nWrite the two paragraphs now.")

    user_prompt = "\n".join(user_parts)

    # Rule-O gate: deficit / teardown language is only legitimate on a genuinely
    # negative-effect song. orange/red -> allow it; everything else (violet / blue
    # / green / unknown) is held to "report the real effect, no manufactured
    # downside."
    allow_deficit = rubric_color in ("orange", "red")

    from app.services.prose_tell_guard import hard_findings
    from app.services.prose_judge import judge as _judge

    def _issues(p: str) -> list:
        """Regex hard findings + semantic-judge findings; all treated hard. The
        judge is fail-soft (a broken judge adds nothing; the regex floor stands)."""
        reg = hard_findings(p, lane="listener", allow_deficit=allow_deficit)
        try:
            sem = _judge(p, lane="listener", positive=not allow_deficit)
        except Exception:
            logger.exception("prose_judge failed (non-fatal) for %s / %s", title, artist)
            sem = []
        return list(reg) + list(sem)

    def _isum(issues) -> str:
        return ", ".join(getattr(f, "code", "?") for f in issues) or "clean"

    def _clean(raw: str) -> Optional[str]:
        """Normalize + lyric-guard + sanity + word-cap one raw model output.
        None if it fails the basic sanity gates."""
        import re
        raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
        if raw.startswith('"') and raw.endswith('"') and raw.count('"') == 2:
            raw = raw[1:-1].strip()
        # Verbatim-lyric lock: strip any sentence that reproduces the lyrics.
        if lyrics:
            from app.services.lyric_quote_guard import strip_verbatim_quotes
            raw, stripped = strip_verbatim_quotes(raw, lyrics)
            if stripped:
                logger.warning("listener_effects_prose carried verbatim lyric quotes for %s / %s; stripped",
                               title, artist)
        # Basic sanity: at least 2 paragraphs, at least 100 chars.
        if len(raw) < 100 or "\n\n" not in raw:
            logger.warning("listener_effects_prose failed sanity (len/paragraphs) for %s / %s", title, artist)
            return None
        # Hard word cap (trim to the last complete sentence under the cap).
        return trim_to_word_cap(raw, LISTENER_MAX_WORDS)

    def _attempt(system: str) -> Optional[str]:
        """One model call + clean. None on API error or sanity failure."""
        try:
            client = Anthropic(api_key=settings.anthropic_api_key)
            response = tracked_create(
                client,
                call_site="listener_effects_prose",
                context={"title": title, "artist": artist, "rubric_color": rubric_color},
                model=AGENT_MODEL,
                max_tokens=800,
                # Retune: exemplars carry the control the ban-wall used to, so
                # temperature comes up off 0.3 for burstiness / less default voice.
                temperature=0.7,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = (response.content[0].text or "").strip()
        except Exception:
            logger.exception("listener_effects_prose generation failed for %s / %s", title, artist)
            return None
        return _clean(raw) if raw else None

    # Attempt 1.
    prose = _attempt(LISTENER_EFFECTS_VOICE)
    if prose is None:
        return None
    issues = _issues(prose)

    # Fail-closed regen: regex floor + semantic judge together. If the first
    # attempt trips anything, regenerate ONCE with the findings as a correction and
    # keep whichever is cleaner. If issues STILL survive, ship nothing (return None)
    # so the page falls back to the tier-generic copy. Never publish a tell-ridden
    # reading unread -- the no-human-eye discipline (Lyrical Charger scale).
    if issues:
        logger.info("listener_effects_prose attempt 1 tripped the guard for %s / %s [%s]; regenerating",
                    title, artist, _isum(issues))
        correction = (
            "\n\n## Your previous attempt tripped the voice guard\n"
            "Rewrite the two paragraphs clean, fixing every issue below and keeping "
            "everything that was already working:\n"
            + "\n".join(f'- {f.name}: found "{f.snippet}"' for f in issues)
        )
        prose2 = _attempt(LISTENER_EFFECTS_VOICE + correction)
        if prose2 is not None:
            issues2 = _issues(prose2)
            if len(issues2) < len(issues):
                prose, issues = prose2, issues2

    if issues:
        logger.warning(
            "listener_effects_prose FAIL-CLOSED (NULL) for %s / %s: %d issue(s) survive regen [%s]",
            title, artist, len(issues), _isum(issues),
        )
        return None

    # SEO: the prose should name the song once. Soft check -- log, never fail.
    if title and title.lower() not in prose.lower():
        logger.info("listener_effects_prose did not include the title for %s / %s", title, artist)

    return prose
