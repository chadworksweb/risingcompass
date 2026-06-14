"""Claude calibration engine for song rubric analysis."""

import asyncio
import json
import logging
import re
from typing import Callable

from anthropic import AsyncAnthropic
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Song
from app.services.charge_composition import (
    CompositionError,
    compose,
    evaluate_escalation,
    validate_components,
)
from app.services.claude_meter import tracked_create_async
from app.services.agents.compass_agent_rubric import (
    build_few_shot_examples,
    build_calibration_prompt,
)
from app.services.agents.summary_guard import (
    CORRECTIVE_NUDGE as _SUMMARY_NUDGE,
    summary_from_json_text,
    summary_has_absence_framing,
)

logger = logging.getLogger(__name__)

AGENT_MODEL = settings.agent_model

# The structured format (CALIBRATION_FORMAT) requires an explicit
# "Contamination: none" / "Contamination: <artifact>" line before the VERDICT.
# This is a binary determination made independently of charge_value; folding an
# artifact into the charge and skipping the flag is the failure this guards.
_CONTAM_LINE_RE = re.compile(r"(?im)^\s*Contamination:\s*\S")


# Calibration methods authoritative enough to serve as a cache hit -- a crowd
# (lyrical_charger / stream) calibration never pre-empts a fresh authoritative
# read. Mirrors the legacy compass-only cache scope on the unified model.
_AUTHORITATIVE_METHODS = {"chart_reading", "catalog_backfill", "terminal"}


def lookup_calibrated(title: str, artist: str, db: Session) -> dict | None:
    """Look up an existing AUTHORITATIVE calibration from the unified songs table.

    Match on canonical_key (the normalized title+primary-artist identity, which
    already subsumes the old punctuation-insensitive fallback). Returns a
    calibration dict or None. A song is a usable cache hit only when it is fully
    calibrated (rubric_color, charge_value, charge_summary) AND its canonical
    calibration was set by an authoritative method -- so the daily read / LC
    don't reuse a crowd calibration, exactly as the legacy compass-only cache did.
    """
    from app.services.song_identity import resolve_song_identity
    # Identity ladder: exact canonical_key, then the cleaned key -- so a feeder
    # re-entry of an already-calibrated song (MV cruft / VEVO-or-label artist)
    # is a CACHE HIT instead of being re-listed as awaiting-lyrics. This is the
    # rung that resolves the 2026-06-13 daily-reading misses.
    resolution = resolve_song_identity(db, title, artist)
    existing = (
        db.query(Song).filter(Song.id == resolution.song_id).first()
        if resolution.song_id else None
    )

    if not existing:
        return None
    # A lyrics_unavailable song is fully RESOLVED (released, but its lyrics are
    # genuinely unobtainable) -- a permanent NULL-tier cache hit, so a live feeder
    # stops re-listing it as awaiting-lyrics every day. It carries no tier/charge
    # and is excluded from every aggregate. Return it BEFORE the incomplete-
    # calibration rejection below (which requires a tier the song will never have).
    if getattr(existing, "lyrics_unavailable", False):
        logger.info("Cache hit (lyrics unavailable) for '%s' by %s (id=%s)",
                    title, artist, existing.id)
        return {
            "song_id": existing.id,
            "rubric_color": None,
            "charge_value": None,
            "charge_summary": None,
            "lyrics_unavailable": True,
            "instrumental": bool(existing.instrumental),
            "contaminated": False,
            "contamination_note": None,
            "dogma_referenced": False,
            "dogma_note": None,
            "confidence": 1.0,
        }
    # Instrumentals carry no charge by design: charge_value and charge_summary
    # stay NULL, the song shows a grey dot, and it is excluded from every
    # aggregate. It is still fully RESOLVED -- there are no lyrics to read -- so
    # a live feeder (Shazam / YouTube) that re-fetches the same instrumental
    # every day must treat it as a cache hit, not re-list it as awaiting-lyrics
    # forever. Only a tier (rubric_color) is required for an instrumental cache
    # hit; non-instrumentals still need a complete charge_value + charge_summary.
    if not existing.instrumental and (
        not existing.rubric_color
        or existing.charge_value is None
        or existing.charge_summary is None
    ):
        logger.warning("Incomplete calibration for '%s' by %s (id=%s) — missing %s",
                       title, artist, existing.id,
                       ", ".join(f for f, v in [
                           ("rubric_color", existing.rubric_color),
                           ("charge_value", existing.charge_value),
                           ("charge_summary", existing.charge_summary),
                       ] if not v and v != 0))
        return None
    if existing.instrumental and not existing.rubric_color:
        # An instrumental still needs a tier to render its row; without one it is
        # not yet a usable cache hit.
        logger.warning("Instrumental '%s' by %s (id=%s) has no rubric_color",
                       title, artist, existing.id)
        return None
    if (existing.canonical_calibration_method or "") not in _AUTHORITATIVE_METHODS:
        # Calibrated, but only by a crowd method -- not a cache hit; the caller
        # runs a fresh (authoritative) calibration just as it did pre-unification.
        return None
    logger.info("Using cached calibration for '%s' by %s: %s %s",
                title, artist, existing.rubric_color, existing.charge_value)

    # Return the full stored object, including the generated fields (ether
    # tags + prose). Callers that hit cache can persist a complete calibration
    # without re-running the model; ensure_full_calibration() fills any that
    # are still NULL on older rows. topics / topic_audit are stored as JSON
    # strings; parse them back to the list / dict shape tag_song emits.
    return {
        "song_id": existing.id,
        "rubric_color": existing.rubric_color,
        "charge_value": existing.charge_value,
        "instrumental": bool(existing.instrumental),
        "contaminated": existing.contaminated or False,
        "contamination_note": existing.contamination_note,
        "dogma_referenced": getattr(existing, "dogma_referenced", None) or False,
        "dogma_note": getattr(existing, "dogma_note", None),
        "charge_summary": existing.charge_summary,
        "confidence": 1.0,
        "listener_effects_prose": getattr(existing, "listener_effects_prose", None),
        "societal_effects_prose": getattr(existing, "societal_effects_prose", None),
        # Carry the cached row's sealed provenance forward so a cache-hit
        # re-persist (e.g. into a new submitted/stream row) keeps the original
        # generated_at + model rather than re-stamping at insert.
        "societal_prose_generated_at": getattr(existing, "societal_prose_generated_at", None),
        "societal_prose_model": getattr(existing, "societal_prose_model", None),
        "deadpan_line": getattr(existing, "deadpan_line", None),
        "topics": _load_json(getattr(existing, "topics", None)),
        "topic_audit": _load_json(getattr(existing, "topic_audit", None)),
    }


def _load_json(raw):
    """Parse a JSON column value back to a Python object. Returns None on
    empty / invalid input so the generation gap-fill treats it as missing."""
    if not raw:
        return None
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


async def _ensure_generation(
    title: str, artist: str, lyrics: str, calib: dict,
    progress_cb: Callable[[str], None] | None = None,
) -> None:
    """Complete a calibration's generated fields IN PLACE: effects prose,
    ether tagging (deadpan_line + topics + topic_audit), then societal prose.

    This is the one and only place the calibration's generation steps (effects,
    ether, societal) are orchestrated. Every in-road reaches it through
    calibrate_song_async / ensure_full_calibration -- nobody re-implements
    "calibrate then tag then prose" on the side.

    Idempotent: any field already present (terminal-supplied or cached) is
    left untouched, so re-running fills only what's missing. Each step fails
    soft -- on error the field stays as-is and the page falls back to
    tier-generic copy. The sync step functions run in threads so the event
    loop isn't blocked through the multi-second model calls; no DB session is
    held here (the caller owns persistence).
    """
    color = calib.get("rubric_color")
    if not color:
        return

    # 1. Effects prose -- what the words may do to a listener.
    if progress_cb:
        progress_cb("listener")
    if not calib.get("listener_effects_prose"):
        try:
            from app.services.listener_effects_prose import generate_listener_effects_prose
            calib["listener_effects_prose"] = await asyncio.to_thread(
                generate_listener_effects_prose,
                title=title, artist=artist, rubric_color=color,
                charge_value=calib.get("charge_value"),
                charge_summary=calib.get("charge_summary"),
                contaminated=bool(calib.get("contaminated", False)),
                contamination_note=calib.get("contamination_note"),
                lyrics=lyrics,
            )
        except Exception:
            logger.exception("listener_effects_prose step failed for %s / %s", title, artist)

    # 2. Ether tagging -- names what the song IS: deadpan_line + topic tags.
    if progress_cb:
        progress_cb("ether")
    if not calib.get("deadpan_line"):
        try:
            from app.services.agents.ether_tagger import tag_song
            ether = await asyncio.to_thread(
                tag_song,
                title=title, artist=artist, lyrics=lyrics,
                rubric_color=color, charge_value=calib.get("charge_value"),
                charge_summary=calib.get("charge_summary"),
                listener_effects_prose=calib.get("listener_effects_prose"),
            )
            if ether:
                calib["deadpan_line"] = ether.get("deadpan_line")
                calib["topics"] = ether.get("topics")
                calib["topic_audit"] = ether.get("topic_audit")
        except Exception:
            logger.exception("ether_tagging step failed for %s / %s", title, artist)

    # 3. Societal prose -- what running this program at scale does to a society.
    #    Grounded on the ether tags + listener prose produced above.
    if progress_cb:
        progress_cb("societal")
    if not calib.get("societal_effects_prose"):
        try:
            from app.services.societal_effects_prose import generate_societal_effects_prose
            soc = await asyncio.to_thread(
                generate_societal_effects_prose,
                title=title, artist=artist, rubric_color=color,
                charge_value=calib.get("charge_value"),
                charge_summary=calib.get("charge_summary"),
                contaminated=bool(calib.get("contaminated", False)),
                contamination_note=calib.get("contamination_note"),
                lyrics=lyrics,
                deadpan_line=calib.get("deadpan_line"),
                topics=calib.get("topics"),
                listener_effects_prose=calib.get("listener_effects_prose"),
            )
            # Carry the sealed provenance alongside the prose so every persist
            # site can write generated_at + model in lockstep. Fail-soft: on
            # None nothing is set and callers write nothing.
            if soc:
                calib["societal_effects_prose"] = soc.prose
                calib["societal_prose_generated_at"] = soc.generated_at
                calib["societal_prose_model"] = soc.model
        except Exception:
            logger.exception("societal_effects_prose step failed for %s / %s", title, artist)


async def ensure_full_calibration(
    title: str, artist: str, lyrics: str | None, calibration: dict,
    progress_cb: Callable[[str], None] | None = None,
) -> dict:
    """Gap-fill the generated fields on an existing calibration dict (e.g. a
    cache hit) through the one shared generation step. Returns the same dict,
    mutated. No-op without lyrics (ether + prose need them)."""
    if lyrics:
        await _ensure_generation(title, artist, lyrics, calibration, progress_cb)
    return calibration


async def _read_v3(
    client: AsyncAnthropic,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    title: str,
    artist: str,
    target_year: int | None,
) -> dict | None:
    """Run ONE calibration read at `model`: call, split reasoning from JSON,
    run the output guards, parse, and validate into v3 components -- with one
    corrective retry shared across every failure kind. Failure kinds:

      1. CONTAMINATION guard -- the format requires an explicit
         "Contamination:" line in the reasoning (guard_trips).
      2. charge_summary absence/verdict framing (guard_trips; see
         summary_guard.py).
      3. Unparseable JSON or components failing validation (parse_retries).

    Guards keep the v2 proceed-with-warning posture: a read whose components
    validate but whose guards still trip after the retry is returned with
    guard_trip_survived=True (an escalation trigger). A read whose JSON never
    validates returns None -- the caller turns that into the explicit
    needs-human-review failure, never a defaulted verdict."""
    messages = [{"role": "user", "content": user_prompt}]
    guard_trips = 0
    parse_retries = 0
    best: dict | None = None
    attempts = 2
    for attempt in range(1, attempts + 1):
        response = await tracked_create_async(
            client,
            call_site="calibrator",
            context={"title": title, "artist": artist, "target_year": target_year},
            model=model,
            max_tokens=3500,
            temperature=0,
            system=system_prompt,
            messages=messages,
        )
        raw = response.content[0].text.strip()

        # Split reasoning from JSON -- reasoning comes first, JSON starts at
        # the first {. Strip a trailing ``` fence if the JSON got wrapped.
        reasoning = ""
        json_str = raw
        brace_idx = raw.find("{")
        if brace_idx > 0:
            reasoning = raw[:brace_idx].strip()
            json_str = raw[brace_idx:]
        if json_str.rstrip().endswith("```"):
            json_str = json_str.rstrip()[:-3]
        json_str = json_str.strip()

        problems: list[str] = []
        corrective_parts: list[str] = []

        contam_ok = bool(_CONTAM_LINE_RE.search(reasoning))
        summary_ok = not summary_has_absence_framing(
            summary_from_json_text(json_str)
        )
        guards_ok = contam_ok and summary_ok
        if not guards_ok:
            guard_trips += 1
        if not contam_ok:
            problems.append("omitted the mandatory 'Contamination:' line")
            corrective_parts.append(
                "Re-run the full structured format with an explicit "
                "'Contamination: none' or 'Contamination: <artifact>' line "
                "before the VERDICT. "
            )
        if not summary_ok:
            problems.append("used absence/verdict framing in charge_summary")
            corrective_parts.append(_SUMMARY_NUDGE)

        parsed = None
        components = None
        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError:
            parse_retries += 1
            problems.append("output did not end in a parseable JSON object")
            corrective_parts.append(
                "Re-emit the full structured reasoning followed by ONE valid "
                "JSON object in the exact required shape. "
            )
        if parsed is not None:
            try:
                components = validate_components(parsed)
            except CompositionError as exc:
                parse_retries += 1
                problems.append(f"JSON failed component validation ({exc})")
                corrective_parts.append(
                    "Fix the JSON fields named above to the required types and "
                    "ranges from the JSON contract. "
                )

        read = None
        if components is not None:
            read = {
                "raw": raw,
                "reasoning": reasoning,
                "parsed": parsed,
                "components": components,
                "confidence": float(parsed.get("confidence") or 0.0),
                "guard_trips": guard_trips,
                "parse_retries": parse_retries,
                "guard_trip_survived": not guards_ok,
                "model": model,
            }
        if read is not None:
            # A usable read -- components validated and the charge composes.
            # Ship it immediately, whether the soft guards (contamination line /
            # charge_summary framing) were clean or drifted. A drifted guard is
            # already recorded on the read (guard_trip_survived) and surfaces as
            # an escalation signal downstream, exactly as it did when the old
            # second pass also tripped. We deliberately do NOT spend a second
            # full-rubric Opus call just to re-coax formatting: that turned every
            # submission into TWO calibrator calls (the dominant latency in the
            # public charger). The corrective retry below is reserved for the
            # genuine no-usable-read case -- unparseable JSON or failed component
            # validation -- where a second attempt is the only path to a result.
            return read

        logger.warning(
            "calibrator output problems for '%s' by %s at %s (attempt %d/%d): %s",
            title, artist, model, attempt, attempts, "; ".join(problems),
        )
        if attempt < attempts:
            corrective = (
                "Your response had these problems: " + "; ".join(problems)
                + ". " + "".join(corrective_parts)
            )
            messages = [
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": raw},
                {"role": "user", "content": corrective},
            ]
    return best


async def calibrate_song_async(
    title: str,
    artist: str,
    lyrics: str | None = None,
    db: Session | None = None,
    target_year: int | None = None,
    skip_cache: bool = False,
    progress_cb: Callable[[str], None] | None = None,
) -> dict:
    """The calibration path. Calibrate a song against the rubric, then complete
    the generated fields (effects prose, ether tagging, societal prose) in one
    pass. Returns a single complete calibration object that every in-road
    persists as-is.

    Generation runs whenever lyrics are present and is idempotent -- a cache
    hit or terminal-supplied field is reused, never regenerated. Preferred
    entry point from async request handlers so asyncio.to_thread isn't needed.
    """
    # Check for existing calibration first. A cache hit still goes through
    # generation gap-fill so older rows missing ether/prose get completed.
    if db and not skip_cache:
        existing = lookup_calibrated(title, artist, db)
        if existing:
            return await ensure_full_calibration(
                title, artist, lyrics, existing, progress_cb
            )

    # No lyrics, no read. A calibration cannot exist without the page, so
    # return the explicit null result without burning an API call (v3
    # short-circuit; the old path spent a full rubric-sized Opus call to be
    # told "null").
    if not lyrics:
        logger.info("No lyrics for '%s' by %s; returning null calibration "
                    "without an API call", title, artist)
        return _null_result(title, artist)

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    # Few-shot examples disabled. Today's corpus skews 1960s and creates a
    # self-reinforcing loop (today's call becomes tomorrow's example), so the
    # rubric stands alone. The tenet definitions + the curated precedent table
    # carry the anchoring without a corpus draw.
    examples = ""

    system_prompt, user_prompt = build_calibration_prompt(
        title, artist, lyrics=lyrics, examples=examples
    )

    # First pass at the default model. The model emits components only; the
    # server composes the charge and derives the tier (charge_composition).
    if progress_cb:
        progress_cb("calibrating")
    read = await _read_v3(
        client, AGENT_MODEL, system_prompt, user_prompt,
        title=title, artist=artist, target_year=target_year,
    )
    if read is None:
        # Output never validated, even after the corrective retry. Explicit
        # needs-human-review failure -- never a defaulted verdict.
        logger.error("Calibration read failed validation for %s by %s", title, artist)
        return _fallback_result(title, artist, "")

    composed = compose(read["components"])
    triggers = evaluate_escalation(
        composed, read["components"],
        guard_trip_survived=read["guard_trip_survived"],
        # Server feeders read original-language lyrics; translated runs exist
        # only on the terminal path, which stamps its own flag.
        translated=False,
        confidence=read["confidence"],
        confidence_floor=settings.escalation_confidence_floor,
    )

    # Escalation gate (spec 2.4). Triggers are ALWAYS recorded on the run;
    # the re-pass only fires when config enables it AND the escalation model
    # actually differs (with Opus-everywhere defaults the gate logs only).
    escalated = False
    first_pass = None
    escalation_model = settings.escalation_model or AGENT_MODEL
    if triggers and settings.escalation_repass_enabled and escalation_model != AGENT_MODEL:
        logger.warning("Escalation re-pass for '%s' by %s (triggers: %s)",
                       title, artist, ", ".join(triggers))
        repass = await _read_v3(
            client, escalation_model, system_prompt, user_prompt,
            title=title, artist=artist, target_year=target_year,
        )
        if repass is not None:
            first_pass = {
                "model": AGENT_MODEL,
                "charge": composed.charge,
                "tier": composed.rubric_color,
                "triggers": triggers,
            }
            read = repass
            composed = compose(read["components"])
            triggers = evaluate_escalation(
                composed, read["components"],
                guard_trip_survived=read["guard_trip_survived"],
                translated=False,
                confidence=read["confidence"],
                confidence_floor=settings.escalation_confidence_floor,
            )
            escalated = True
    elif triggers:
        logger.warning("Escalation triggers recorded for '%s' by %s (no re-pass): %s",
                       title, artist, ", ".join(triggers))

    if read["reasoning"]:
        logger.info("Agent reasoning for '%s' by %s:\n%s", title, artist, read["reasoning"])

    c = read["components"]
    parsed = read["parsed"]

    # Contamination is cross-derived from the axis data (a discrete harm
    # artifact on a read the harm axis does not govern). The model's own flag
    # is a cross-check: a mismatch is recorded, the derivation wins. The note
    # stays the model's words, kept only when the flag holds.
    contaminated = composed.contaminated
    signals = list(composed.signals)
    if bool(parsed.get("contaminated", False)) != contaminated:
        signals.append("contamination_flag_mismatch")
        logger.warning(
            "contamination flag mismatch for '%s' by %s: model=%s derived=%s",
            title, artist, bool(parsed.get("contaminated", False)), contaminated,
        )

    escalation_flags = None
    if triggers or signals or first_pass:
        escalation_flags = {"triggers": triggers, "signals": signals}
        if first_pass:
            escalation_flags["first_pass"] = first_pass

    calibration = {
        "rubric_color": composed.rubric_color,
        "charge_value": composed.charge,
        "contaminated": contaminated,
        "contamination_note": parsed.get("contamination_note") if contaminated else None,
        "dogma_referenced": bool(parsed.get("dogma_referenced", False)),
        "dogma_note": parsed.get("dogma_note"),
        "charge_summary": parsed.get("charge_summary", ""),
        "confidence": read["confidence"],
        # The agent's structured argument (the prose before the JSON). Carried
        # through to the calibration run, where log_run's _guard_reasoning
        # scrubs any verbatim lyric runs before it is stored.
        "reasoning": read["reasoning"] or None,
        # v3 components + incoherence signals -> calibration_runs columns
        # (log_run). Internal-only; song_sync's column whitelist keeps them
        # off the songs row.
        "visceral_charge": c.visceral_charge,
        "route": c.route,
        "harm": {"value": c.harm_value, "pervasive": c.harm_pervasive},
        "transcendence": {"value": c.transcendence_value},
        "governing_axis": composed.governing_axis,
        "center": c.center,
        "vernier": c.vernier,
        "precedent_refs": c.precedent_refs,
        "gut_divergence": composed.gut_divergence,
        "guard_trips": read["guard_trips"],
        "parse_retries": read["parse_retries"],
        "escalation_flags": escalation_flags,
        "escalated": escalated,
    }

    # If the summary STILL carries absence/verdict framing after the retry, ship
    # it (a false-positive must never blank a valid summary) but log loudly so the
    # drift is visible. Mirrors the proceed-with-warning posture of the guards.
    if lyrics and summary_has_absence_framing(calibration["charge_summary"]):
        logger.warning(
            "charge_summary retained absence/verdict framing after retry for '%s' by %s: %r",
            title, artist, calibration["charge_summary"],
        )

    # Verbatim-lyric backstop on the calibrator's quote-prone short fields, both of
    # which render on the public song page. The rubric now asks for paraphrase; if a
    # verbatim run slips through anyway, clear the field so no copyrighted lyric text
    # ever ships. contaminated / dogma_referenced flags stay set, so the indicators
    # still show and the page falls back to generic copy.
    if lyrics:
        from app.services.lyric_quote_guard import has_verbatim_overlap
        for _field in ("contamination_note", "dogma_note"):
            if calibration.get(_field) and has_verbatim_overlap(calibration[_field], lyrics):
                logger.warning("%s carried verbatim lyric quotes for %s / %s; cleared",
                               _field, title, artist)
                calibration[_field] = None

    # Complete the generated fields (effects prose, ether tagging, societal
    # prose) in the same pass -- the calibration path returns one whole object.
    if lyrics:
        await _ensure_generation(title, artist, lyrics, calibration, progress_cb)

    return calibration


def calibrate_song(
    title: str,
    artist: str,
    lyrics: str | None = None,
    db: Session | None = None,
    target_year: int | None = None,
    skip_cache: bool = False,
) -> dict:
    """Sync wrapper around calibrate_song_async. For scripts and legacy sync
    callers (e.g. compass_agent.run_compass_agent)."""
    return asyncio.run(calibrate_song_async(
        title, artist, lyrics=lyrics, db=db,
        target_year=target_year, skip_cache=skip_cache,
    ))


def _fallback_result(title: str, artist: str, raw_response: str) -> dict:
    """Return an explicit failure when Claude's response can't be parsed or
    validated. rubric_color=None signals the song needs human intervention
    rather than silently defaulting to green/0; calibration_failed stamps the
    run so the failure stays visible in the ledger.
    """
    return {
        "rubric_color": None,
        "charge_value": None,
        "contaminated": False,
        "contamination_note": None,
        "dogma_referenced": False,
        "dogma_note": None,
        "charge_summary": f"Calibration failed — manual review needed for {title} by {artist}",
        "confidence": 0.0,
        "calibration_failed": True,
    }


def _null_result(title: str, artist: str) -> dict:
    """The null calibration for a song with no lyrics: nothing was read, so
    nothing is scored. Distinct from _fallback_result (a read that failed)."""
    return {
        "rubric_color": None,
        "charge_value": None,
        "contaminated": False,
        "contamination_note": None,
        "dogma_referenced": False,
        "dogma_note": None,
        "charge_summary": f"No lyrics available for {title} by {artist}; awaiting lyrics to calibrate",
        "confidence": 0.0,
    }
