"""Claude calibration engine for song rubric analysis."""

import asyncio
import json
import logging
from typing import Callable

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Song

logger = logging.getLogger(__name__)

# Re-exported for compass_agent (which reads it for run metadata + logging). The
# scoring model itself now lives in LEC; this stays only as a convenience handle.
AGENT_MODEL = settings.agent_model


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
    allow_generation: bool = True,
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

    allow_generation=False turns every generate step OFF: present fields are
    still reused, but a MISSING field is left as-is (never generated). This is
    how the local "Claude Code is the model" seam guarantees zero Anthropic
    even if the supplied calibration forgot a prose/ether field.
    """
    color = calib.get("rubric_color")
    if not color:
        return

    # 1. Effects prose -- what the words may do to a listener.
    if progress_cb:
        progress_cb("listener")
    if allow_generation and not calib.get("listener_effects_prose"):
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
    if allow_generation and not calib.get("deadpan_line"):
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
    if allow_generation and not calib.get("societal_effects_prose"):
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
    allow_generation: bool = True,
) -> dict:
    """Gap-fill the generated fields on an existing calibration dict (e.g. a
    cache hit) through the one shared generation step. Returns the same dict,
    mutated. No-op without lyrics (ether + prose need them)."""
    if lyrics:
        await _ensure_generation(title, artist, lyrics, calibration, progress_cb,
                                 allow_generation=allow_generation)
    return calibration


def _scrub_calibration_quotes(
    calibration: dict, lyrics: str | None, title: str = "", artist: str = "",
) -> None:
    """Clear the calibrator's quote-prone note fields (contamination_note,
    dogma_note) IN PLACE if they reproduce a verbatim lyric run (>= 6 words).
    The contaminated / dogma_referenced flags stay set, so the indicators still
    show and the page falls back to generic copy. No-op without lyrics.

    Applied to BOTH the in-process read and the LEC read, so neither engine can
    ship copyrighted lyric text in a note. (The longer prose fields are scrubbed
    later at the storage chokepoint via lyric_quote_guard.scrub_calibration_quotes;
    these two short fields are guarded here because they exist pre-generation.)
    """
    if not lyrics:
        return
    from app.services.lyric_quote_guard import has_verbatim_overlap
    for field in ("contamination_note", "dogma_note"):
        if calibration.get(field) and has_verbatim_overlap(calibration[field], lyrics):
            logger.warning("%s carried verbatim lyric quotes for %s / %s; cleared",
                           field, title, artist)
            calibration[field] = None


async def calibrate_song_async(
    title: str,
    artist: str,
    lyrics: str | None = None,
    db: Session | None = None,
    target_year: int | None = None,
    skip_cache: bool = False,
    progress_cb: Callable[[str], None] | None = None,
    supplied: dict | None = None,
    allow_generation: bool = True,
) -> dict:
    """The calibration path. Calibrate a song against the rubric, then complete
    the generated fields (effects prose, ether tagging, societal prose) in one
    pass. Returns a single complete calibration object that every in-road
    persists as-is.

    Generation runs whenever lyrics are present and is idempotent -- a cache
    hit or terminal-supplied field is reused, never regenerated. Preferred
    entry point from async request handlers so asyncio.to_thread isn't needed.

    supplied: the local "Claude Code is the model" seam. When a supplied
    calibration dict is passed, it REPLACES the scoring step -- LEC/Anthropic is
    NOT called; the supplied read is scrubbed + enriched (generation gated by
    allow_generation) and returned. The router gates this to a local backend
    with the flag on, so it can never fire in prod. No cache lookup on this path
    (the supplied read is authoritative for this run).
    """
    if supplied is not None:
        import copy
        calib = copy.deepcopy(supplied)
        if lyrics:
            _scrub_calibration_quotes(calib, lyrics, title, artist)
            await _ensure_generation(title, artist, lyrics, calib, progress_cb,
                                     allow_generation=allow_generation)
        logger.info("Scored '%s' by %s via SUPPLIED calibration (local model seam)",
                    title, artist)
        return calib

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

    # LEC is the sole scorer (Phase 3). RC no longer carries an in-process
    # calibrator: the scoring brain was extracted into the Libra Engine Compass
    # (LEC) service so calibrator tuning stops colliding with RC feature work.
    # Score through LEC over HTTP, map the result back into this calibration
    # shape, then run the same verbatim-quote guard + generation the path has
    # always run. A LEC failure (unreachable / non-200 / unscorable) returns the
    # explicit needs-human-review result -- never a defaulted verdict, and never
    # a silent in-process re-score, because there is no in-process scorer left.
    from app.services import lec_client
    lec_calibration = await lec_client.score_via_lec(
        title, artist, lyrics, artifact_type="lyric",
    )
    if lec_calibration is None:
        logger.error("LEC scoring failed for '%s' by %s; needs human review",
                     title, artist)
        return _fallback_result(title, artist, "")

    logger.info("Scored '%s' by %s via LEC", title, artist)
    # Clear any verbatim lyric run from the calibrator's short note fields, then
    # complete the generated fields (effects prose, ether tagging, societal
    # prose) -- the calibration path returns one whole object.
    _scrub_calibration_quotes(lec_calibration, lyrics, title, artist)
    await _ensure_generation(title, artist, lyrics, lec_calibration, progress_cb)
    return lec_calibration


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
