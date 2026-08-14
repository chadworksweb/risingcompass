"""Album-level synthesis generator (Album Charger).

After every track on an album has been calibrated, this runs ONE Opus call
that reads the per-track results (title, tier, charge, one-line summary,
contamination) and the computed album aggregate, and synthesizes the
album-level reading:

  charge_summary  -- one paragraph: what the album, taken whole, transmits.
  arc_prose       -- how the album moves across its running order: where it
                     opens, where it peaks or sinks, where it lands.
  listener_effects_prose   -- what the whole album does to a LISTENER (album-scale parallel
                     of a song's listener reading).
  societal_effects_prose  -- what running this album at scale does to a society.
  deadpan_line / topics / topic_audit -- the album's Ether Art Chart entry
                     (flat naming + 0-3 taxonomy slugs), parallel to a song's.

Mirrors listener_effects_prose.py: synchronous, runs on Opus through tracked_create,
fails soft. On any failure the caller stores NULL and the album page falls
back to the bare aggregate (charge + tier + per-track breakdown).

The model is given ONLY the calibration data per track, never lyrics -- the
per-track readings already distilled the lyrics, and an album can be 40
tracks. Synthesis reasons over the readings, not the raw words.
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

TIER_LABELS = {
    "violet": "Ascended",
    "blue": "Elevated",
    "green": "Decent",
    "orange": "Degraded",
    "red": "Corrupted",
}


# ============================== QUARANTINED ==============================
# PRE-RETUNE BAN-WALL, still live on the Album Charger's synthesis path.
#
# The 2026-07-09 editorial voice retune (RC-VOICE-RETUNE-STANDARD.md, archived)
# explicitly scoped "the terminal-supplied prose (charge_summary, editorial,
# album synthesis)" alongside the two server prose prompts, on the finding that
# long negative "never" lists fail: a ban is a soft constraint against a hard
# training prior, so banning one construction just promotes the next one. The
# replacement was a short POSITIVE spec + gold exemplars + a code-side validator.
#
# The retune reached listener_effects_prose.py and societal_effects_prose.py on
# 2026-07-10 (870b3c3). This constant has not been touched since 2026-05-26
# (6051772, the Album Charger build) -- ~32 prohibitions, ZERO exemplars.
#
# NOTE (corrected 2026-08-14): this was flagged alongside a claim that the
# missing retune explains weak summary prose. That claim did not survive the
# corpus -- albums read under the un-retuned block produced accepted summaries.
# The drift is real; the causal story was not.
#
# Its sibling on the same footing is
# libra-engine-compass backend/app/services/agents/lec_compass_agent_rubric.py
# :: SUMMARY_VOICE_RULES, which drives charge_summary for every song and album.
#
# Scope note: this drives the ALBUM CHARGER's server-side synthesis only. The
# rc-album v3 lens (the terminal album read) does not use it.
# =========================================================================
ALBUM_VOICE = """You are writing the album-level reading on a Rising Compass album page.

The song is the atomic unit. Every track on this album has ALREADY been read individually -- each one has its own listener reading (what the words do to a person) and its own societal reading (what running that song at scale does to a society). Your job is NOT to re-analyze the album as one lump. Your job is to read those finished per-song readings as a body of work and compile the album-level reading FROM them.

You are given, per track: its tier and charge, its one-line calibration summary, its listener reading, and its societal reading. You never see lyrics. Ground every sentence you write in what those per-song readings already say. Do not introduce claims that none of the song readings support. You are synthesizing across atomic readings, not inventing a new analysis on top of them.

## What you produce

You output ONLY a JSON object with these keys: "charge_summary", "arc_prose", "listener_effects_prose", "societal_effects_prose", "deadpan_line", "topics", "topic_audit". No preamble, no markdown fences, no commentary outside the JSON.

- charge_summary: ONE paragraph, 2 to 4 sentences. What the album, taken whole, transmits, drawn from the common threads across the individual listener readings. The dominant posture. Name it plainly.
- arc_prose: ONE paragraph, 3 to 5 sentences. How the album moves across its running order, as the per-song readings carry it. Where it opens, where it climbs or sinks, where it lands. Reference track positions by what they do, not by reciting titles in order. If the readings sit in the same register throughout, say the album is flat honestly instead of inventing a journey.
- listener_effects_prose: ONE to TWO paragraphs. What taking in this WHOLE album does to a LISTENER, compiled from the individual listener readings. The dominant pulls a person absorbs across the running order, what repeat listening reinforces in them. Address the listener plainly. This is the album-scale parallel of each song's listener reading (distinct from societal_effects_prose, which is the population-scale read).
- societal_effects_prose: ONE paragraph, 2 to 4 sentences. What happens when many people take this whole album in, on repeat, synthesized from the individual societal readings. What gets reinforced at scale. Speak to possibility, not prophecy.
- deadpan_line: a FLAT, literal naming of the WHOLE album, about as long as the artist and title together. Museum-placard register: naming, not commenting, no verdict, no period, no articles if droppable. Name the album's content, never the artist. Descriptive adjectives are allowed ("defiant", "wounded", "carnal"); evaluative ones are forbidden ("shallow", "vapid", "pathetic"). If the read lands flat and the gap between the album's image and its content shows, that is the instrument working, not a joke.
- topics: 0 to 3 tags from the closed taxonomy listed in the user message, ordered most-dominant-first by share of the album's content. The topic that captures the album's center of gravity comes first; the more specific topic wins ties. Cap at 3. If no honest taxonomy match exists, return [] and fill topic_audit.
- topic_audit: null when topics is non-empty. When topics is empty, an object with keys "reason", "proposed_tag", "rationale". Exactly one of (topics non-empty, topic_audit non-null) is true.

## The compass voice

- Authoritative. State what IS. No hedging, no "could be," no "might be interpreted as."
- Sharp when the album earns sharp. Warm when it earns warm. The readings set the temperature.
- Plain language hits harder than writerly language. Vary sentence length naturally.
- Dry wit when the content invites it.

## Hard "never" list

- Never reference Rising Compass, tiers, colors, charge numbers, or calibration vocabulary in the prose.
- Never use em-dashes. Use commas, periods, or parentheses.
- Never use "Normalizes," "Activates," "Models," "Wrapped in," "Framed as," "Baked in," "In today's anything."
- Never use therapist vocabulary ("trauma response," "coping mechanism," "emotional regulation," "processing").
- Never use hedging phrases ("it's worth noting," "that said," "moreover," "furthermore").
- Never use "at the end of the day," "bottom line," "in short," "simply put."
- Never use empty intensifiers ("truly," "really," "incredibly," "absolutely").
- Never use polar-opposite contrast structures ("not X, but Y").
- Never use the "from X to Y" / "what starts as X becomes Y" progression as a crutch (the arc paragraph may describe real movement, but not in that canned shape).
- Never use passive voice.
- Never moralize. Don't say the album is "good" or "bad."
- Never write a rhetorical question to close a paragraph.
- Profanity censoring: f**k, s**t, c**t, b***h (first + last letter, asterisks between). Ass, damn, hell stay uncensored.

Output ONLY the JSON object."""


def _clean_paragraph(text: str | None) -> Optional[str]:
    if not text:
        return None
    text = re.sub(r"\n{3,}", "\n\n", str(text)).strip()
    if text.startswith('"') and text.endswith('"') and text.count('"') == 2:
        text = text[1:-1].strip()
    return text or None


def generate_album_synthesis(
    *,
    album_title: str,
    artist: str,
    release_type: str,
    aggregate_charge: int | None,
    aggregate_color: str | None,
    tracks: list[dict],
) -> dict:
    """Run the album-synthesis agent over the per-track readings.

    `tracks` is a list of dicts per scored song:
      {track_number, title, tier (color), charge, charge_summary, contaminated,
       listener_effects_prose, societal_effects_prose}
    The album reading is compiled FROM these atomic per-song readings -- the
    listener prose (listener_effects_prose) and societal prose are the body of work the
    agent reasons over, not the lyrics. Returns a dict with keys charge_summary,
    arc_prose, societal_effects_prose (any may be missing on soft failure). Returns an
    empty dict if there's nothing to synthesize or the call fails.
    """
    scored = [t for t in tracks if t.get("tier")]
    if not scored:
        return {}

    # Resolve the taxonomy from a short-lived session (DB-driven when the flag is
    # on, else code). Same fail-safe contract as the per-song tagger.
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            taxonomy_text = tax.taxonomy_for_prompt(db)
            valid_slugs = tax.valid_slugs(db)
        finally:
            db.close()
    except Exception:
        logger.exception("album_synthesis taxonomy resolve failed; using code taxonomy")
        taxonomy_text, valid_slugs = tax.taxonomy_for_prompt(), VALID_SLUGS

    agg_label = TIER_LABELS.get(aggregate_color or "", aggregate_color or "")
    type_label = {"album": "album", "ep": "EP", "single": "single"}.get(release_type, "album")

    # Cap each per-song prose so a long tracklist stays a reasonable prompt.
    def _trim(text, limit=700):
        t = (text or "").strip()
        return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0] + "..."

    lines = [
        f'Album: "{album_title}" by {artist}',
        f"Format: {type_label}",
        f"Album aggregate: {agg_label}"
        + (f" (charge {aggregate_charge:+d})" if aggregate_charge is not None else ""),
        f"Scored tracks: {len(scored)} of {len(tracks)}",
        "",
        "The individual song readings, in running order. Compile the album from these:",
    ]
    for t in scored:
        num = t.get("track_number")
        prefix = f"{num}. " if num else "- "
        tier_label = TIER_LABELS.get(t.get("tier", ""), t.get("tier", ""))
        charge = t.get("charge")
        charge_str = f" {charge:+d}" if charge is not None else ""
        contam = " [contaminated]" if t.get("contaminated") else ""
        lines.append("")
        lines.append(f'{prefix}"{t.get("title", "")}" -- {tier_label}{charge_str}{contam}')
        summary = (t.get("charge_summary") or "").strip()
        if summary:
            lines.append(f"   Summary: {summary}")
        listener = _trim(t.get("listener_effects_prose"))
        if listener:
            lines.append(f"   Listener reading: {listener}")
        societal = _trim(t.get("societal_effects_prose"))
        if societal:
            lines.append(f"   Societal reading: {societal}")

    lines.append("")
    lines.append("Ether Art Chart taxonomy (for the album's deadpan_line + topics, dominant-first):")
    lines.append(taxonomy_text)
    lines.append("")
    lines.append("Write the JSON object now, compiled from the song readings above.")
    user_prompt = "\n".join(lines)

    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        response = tracked_create(
            client,
            call_site="album_synthesis",
            context={"album": album_title, "artist": artist, "tracks": len(scored)},
            model=AGENT_MODEL,
            max_tokens=1200,
            temperature=0.3,
            system=ALBUM_VOICE,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = (response.content[0].text or "").strip()
    except Exception:
        logger.exception("album_synthesis generation failed for %s / %s", album_title, artist)
        return {}

    if not raw:
        return {}

    # Pull the JSON object out of the response (strip any stray prose/fences).
    brace = raw.find("{")
    end = raw.rfind("}")
    if brace < 0 or end <= brace:
        logger.warning("album_synthesis returned no JSON object for %s / %s", album_title, artist)
        return {}
    json_str = raw[brace:end + 1]

    try:
        parsed = json.loads(json_str)
    except (ValueError, TypeError):
        logger.warning("album_synthesis JSON parse failed for %s / %s", album_title, artist)
        return {}

    out = {}
    for key in ("charge_summary", "arc_prose", "listener_effects_prose", "societal_effects_prose"):
        cleaned = _clean_paragraph(parsed.get(key))
        if cleaned:
            out[key] = cleaned

    # Album-level Ether Art Chart entry: deadpan_line + topics (+ topic_audit),
    # validated against the closed taxonomy exactly like the per-song ether tagger.
    deadpan = (parsed.get("deadpan_line") or "").strip().strip('"').strip()
    if deadpan:
        out["deadpan_line"] = deadpan

    raw_topics = parsed.get("topics")
    valid = [t for t in raw_topics if isinstance(t, str) and t in valid_slugs][:3] \
        if isinstance(raw_topics, list) else []
    audit = parsed.get("topic_audit")
    if not isinstance(audit, dict):
        audit = None
    # Enforce topics-XOR-audit: topics win; otherwise keep/synthesize an audit so
    # an unmatched album still surfaces for human eyes.
    if valid:
        out["topics"] = valid
        out["topic_audit"] = None
    elif audit:
        out["topics"] = []
        out["topic_audit"] = audit
    else:
        out["topics"] = []
        out["topic_audit"] = {
            "reason": "Album synthesis returned no taxonomy-valid topics and no audit payload.",
            "proposed_tag": "",
            "rationale": "",
        }
    return out
