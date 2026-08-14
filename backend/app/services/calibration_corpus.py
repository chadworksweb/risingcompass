"""Calibration corpus + consensus engine.

Every agent run — wherever it fires — lands in calibration_runs. When a run
targets an existing song, the canonical row drifts toward the MEDIAN of all
its live runs (Calibrator v3: one outlier vote can no longer drag a verdict
toward a boundary). Tier flips from consensus drift are logged as
song_recalibrations entries so the public audit trail stays honest.

This is two things at once: a user-facing consensus ("this song was
calibrated 8 times, consensus is orange -34") and a training corpus for
later model work (input = lyrics hash + song identity, output = full
calibration).

Lyrics themselves are never stored — only a SHA-256 hash for dedupe /
variance awareness.
"""

from __future__ import annotations

import hashlib
import json
import logging
import statistics
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models import (
    CalibrationRun, SongRecalibration, Artist, SongArtist, Song,
)

logger = logging.getLogger(__name__)


# Unified song-entity renovation (Phase 5b): the corpus operates on the atomic
# `songs` row. Calibration runs / recalibrations key off the unified song id
# (CalibrationRun.unified_song_id), so consensus aggregates EVERY run for the
# song -- across the former cross-table / cross-year duplicate rows that merged
# into it. Public signatures (find_canonical_song, compute_consensus) are kept
# stable: legacy (source, id) inputs resolve to the unified song via song_id_map
# (source='songs' means the id is already the unified id).


def resolve_unified_song(
    db: Session, *,
    source: str | None = None, song_id: int | None = None,
    title: str | None = None, artist: str | None = None,
) -> Song | None:
    """Resolve to the unified Song. Priority: explicit (source, id) -- 'songs'
    is the unified id directly, a legacy (source, id) maps via song_id_map --
    then (title, artist) by canonical_key, then the song_artists credit-path
    fallback (mirrors find_canonical_song's second pass)."""
    if song_id and source == "songs":
        return db.query(Song).get(song_id)
    if song_id and source:
        new_id = db.execute(
            text("SELECT new_song_id FROM song_id_map WHERE old_source = :s AND old_id = :i"),
            {"s": source, "i": song_id},
        ).scalar()
        if new_id:
            return db.query(Song).get(new_id)
    if title and artist:
        # Full identity ladder (exact canonical_key -> cleaned key -> trgm dark),
        # not just the exact key. After the feeder-cruft guard a re-entry's RAW
        # string ("ARTIST - Title (Official Music Video)" / "...VEVO") no longer
        # matches the now-clean stored row on the exact key, but the clean rung
        # resolves it -- so the run ledger logs against the right song instead of
        # missing and falling through. Credit-path stays the final fallback.
        from app.services.song_identity import resolve_song_identity
        res = resolve_song_identity(db, title, artist)
        if res.song_id:
            return db.query(Song).get(res.song_id)
        return _find_song_via_credits(db, title, artist)
    return None


def _find_song_via_credits(db: Session, title: str, artist: str) -> Song | None:
    """Credit-path fallback: split the credit string, look the artists up, and
    find a unified song with the same title carrying ANY of them via
    song_artists.song_id. Catches reordered/abbreviated credits."""
    from app.services.artist_linker import parse_artist_string
    t_lower = title.strip().lower()
    artist_ids: list[int] = []
    for e in parse_artist_string(artist):
        name = (e.get("name") or "").strip()
        if not name:
            continue
        a_row = db.query(Artist).filter(func.lower(Artist.name) == name.lower()).first()
        if a_row:
            artist_ids.append(a_row.id)
    if not artist_ids:
        return None
    candidate_ids = {
        sid for (sid,) in (
            db.query(SongArtist.song_id)
            .filter(SongArtist.artist_id.in_(artist_ids))
            .filter(SongArtist.song_id.isnot(None))
            .distinct()
            .all()
        )
    }
    if not candidate_ids:
        return None
    return (
        db.query(Song)
        .filter(Song.id.in_(candidate_ids))
        .filter(func.lower(Song.title) == t_lower)
        .first()
    )


def hash_lyrics(lyrics: str | None) -> str | None:
    """Normalize + hash lyrics so repeat submissions of the same text dedupe.

    Lowercase, collapse whitespace, strip. The hash lets us answer "same
    lyrics seen before?" without storing them.
    """
    if not lyrics:
        return None
    normalized = " ".join(lyrics.lower().split())
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _vibe_snapshot_json(db: Session, source: str, song_id: int) -> Optional[str]:
    """Snapshot the audience-vibe needle at recalibration time.

    Returns JSON {value, pushes_up, pushes_down} for storage on
    SongRecalibration.vibe_snapshot, or None when the needle is untouched
    (no pushes recorded). Fails soft so a consensus-drift recalibration
    never blocks on a vibe read.
    """
    try:
        from app.services.audience_vibe import get_state
        state = get_state(db, source, song_id, device_id=None)
        if state.get("pushes_up_total", 0) == 0 and state.get("pushes_down_total", 0) == 0:
            return None
        return json.dumps({
            "value": state["value"],
            "pushes_up": state["pushes_up_total"],
            "pushes_down": state["pushes_down_total"],
        })
    except Exception:
        logger.exception("vibe_snapshot read failed for %s/%s", source, song_id)
        return None


# THE single tier function (Calibrator v3): tier is always derived from
# charge, server-side, everywhere. Re-exported under the historical private
# name for this module's call sites.
from app.services.charge_composition import derive_tier as _derive_tier  # noqa: E402


def find_canonical_song(title: str, artist: str, db: Session) -> tuple[str, object] | None:
    """Find the canonical unified song for (title, credit_string).

    Returns ("songs", Song) or None. Unified renovation: a single atomic
    `songs` row by canonical_key, with the song_artists credit-path fallback
    that catches reordered/abbreviated credits ("Runway by Doechii" matching a
    prior "Runway by Lady Gaga & Doechii"). The ("songs", id) shape keeps the
    legacy (source, id) call contract -- downstream polymorphic writes now use
    song_source='songs' + the unified id."""
    if not title or not artist:
        return None
    song = resolve_unified_song(db, title=title, artist=artist)
    if song:
        return "songs", song
    return None


# Back-compat alias — older code paths may still import the private name.
_find_canonical_song = find_canonical_song


def _seed_initial_run_if_missing(source: str, song, db: Session):
    """Lazy backfill: if a song has no calibration_runs history yet but is
    already calibrated, log its current state as the initial run so consensus
    starts with proper context. Keyed by the unified song id -- `song` may be a
    unified Song (source='songs') or a legacy row (resolved via song_id_map)."""
    unified = song if source == "songs" else resolve_unified_song(
        db, source=source, song_id=getattr(song, "id", None),
        title=getattr(song, "title", None), artist=getattr(song, "artist", None),
    )
    if unified is None:
        return
    existing_count = (
        db.query(func.count(CalibrationRun.id))
        .filter(CalibrationRun.song_id == unified.id)
        .scalar()
    )
    if existing_count > 0:
        return
    if getattr(song, "charge_value", None) is None:
        # Song is uncalibrated (or was reset) — nothing to seed
        return
    seed = CalibrationRun(
        song_id=unified.id,
        title=getattr(song, "title", None),
        artist=getattr(song, "artist", None),
        rubric_color=getattr(song, "rubric_color", None) or None,
        charge_value=getattr(song, "charge_value", None),
        charge_summary=getattr(song, "charge_summary", None),
        contaminated=bool(getattr(song, "contaminated", False)),
        contamination_note=getattr(song, "contamination_note", None),
        dogma_referenced=bool(getattr(song, "dogma_referenced", False) or False),
        dogma_note=getattr(song, "dogma_note", None),
        confidence=getattr(song, "confidence", None),
        agent_model=None,  # unknown — pre-corpus
        triggered_by="seed",
        lyrics_hash=None,
    )
    db.add(seed)
    db.flush()


def _guard_reasoning(
    reasoning: str | None,
    lyrics: str | None,
    *,
    title: str | None = None,
    artist: str | None = None,
    lyric_free: bool = False,
) -> str | None:
    """The single code-level lock on the stored agent argument: a run's
    `reasoning` is persisted ONLY after passing the verbatim-lyric scrub.

    Fail-closed: if reasoning is supplied but no lyrics are available to check
    it against, nothing is stored (we never persist an unverified argument).
    When lyrics ARE present, any sentence carrying a >= MIN_RUN verbatim lyric
    run is stripped; if that guts the text, None is stored. The lyrics are used
    transiently for comparison only and are NEVER persisted (the corpus stores
    a hash, not the words -- see module docstring).

    `lyric_free` is the ALBUM lane, and it is a narrow, structural exemption
    rather than a bypass. The rc-album lens reads approved song ROWS in running
    order -- summaries, prose and numbers already scrubbed at song scale. No
    lyric text exists anywhere in that lane, so there is nothing to check
    against and nothing that could have been copied: fail-closed would store
    NOTHING, forever, for every album. Declaring the lane is therefore an
    assertion the caller must be able to make truthfully, and passing lyrics
    alongside it contradicts the claim -- so that combination is treated as the
    ordinary lyric lane and scrubbed anyway."""
    if not reasoning:
        return None
    if lyric_free and not lyrics:
        return reasoning
    if lyric_free and lyrics:
        logger.warning(
            "lyric_free run for '%s' by %s was passed lyrics; scrubbing anyway "
            "(the lane claims no lyrics exist)", title, artist,
        )
    if not lyrics:
        logger.warning(
            "reasoning supplied without lyrics for '%s' by %s; not stored "
            "(cannot verify it carries no verbatim lyrics)", title, artist,
        )
        return None
    from app.services.lyric_quote_guard import strip_verbatim_quotes
    cleaned, stripped = strip_verbatim_quotes(reasoning, lyrics)
    if stripped:
        logger.warning(
            "Stripped verbatim lyric quotes from reasoning for '%s' by %s", title, artist,
        )
    return cleaned or None


def log_run(
    db: Session,
    *,
    title: str | None,
    artist: str | None,
    calibration: dict,
    triggered_by: str,
    song_id: int | None = None,
    release_id: int | None = None,
    lyrics_hash: str | None = None,
    lyrics_fingerprint: bytes | None = None,
    agent_model: str | None = None,
    lyrics: str | None = None,
    lyric_free: bool = False,
) -> CalibrationRun:
    """Record one agent run. Always writes. The caller has already committed
    the song row (or decided no song row is appropriate). `song_id` is the
    atomic songs.id and keys the run to the song for consensus aggregation.

    `release_id` keys an ALBUM run instead (migration 149): the rc-album lens
    emits the same v3 component shape, so a release reading is this same ledger
    row with the release pointer set and the song pointer NULL. That lane is
    lyric-free by construction -- it reads approved song rows, never lyrics --
    so it passes `lyric_free=True` and carries no hash and no fingerprint.

    `calibration` may carry a "reasoning" key (the agent's structured argument).
    It is stored ONLY through `_guard_reasoning`, which scrubs verbatim lyrics
    against `lyrics` and fails closed without them. `lyrics` is used for that
    check only and is never persisted.

    Calibrator v3 component + incoherence keys (visceral_charge, route, harm,
    transcendence, governing_axis, center, vernier, precedent_refs,
    gut_divergence, guard_trips, parse_retries, escalation_flags, escalated,
    translated, calibration_failed) map onto the migration-116 columns. All
    optional: legacy/terminal-direct calibration dicts log cleanly with NULLs."""
    harm = calibration.get("harm") or {}
    transcendence = calibration.get("transcendence") or {}
    run = CalibrationRun(
        song_id=song_id,
        release_id=release_id,
        title=title,
        artist=artist,
        rubric_color=calibration.get("rubric_color"),
        charge_value=calibration.get("charge_value"),
        charge_summary=calibration.get("charge_summary"),
        contaminated=bool(calibration.get("contaminated", False)),
        contamination_note=calibration.get("contamination_note"),
        dogma_referenced=bool(calibration.get("dogma_referenced", False)),
        dogma_note=calibration.get("dogma_note"),
        confidence=calibration.get("confidence"),
        agent_model=agent_model,
        triggered_by=triggered_by,
        lyrics_hash=lyrics_hash,
        lyrics_fingerprint=lyrics_fingerprint,
        reasoning=_guard_reasoning(
            calibration.get("reasoning"), lyrics, title=title, artist=artist,
            lyric_free=lyric_free,
        ),
        visceral_charge=calibration.get("visceral_charge"),
        route=calibration.get("route"),
        coherence=calibration.get("coherence"),
        harm_value=harm.get("value"),
        harm_pervasive=bool(harm.get("pervasive", False)),
        transcendence_value=transcendence.get("value"),
        governing_axis=calibration.get("governing_axis"),
        center=calibration.get("center"),
        vernier=_dump_json(calibration.get("vernier")),
        precedent_refs=_dump_json(calibration.get("precedent_refs")),
        gut_divergence=calibration.get("gut_divergence"),
        guard_trips=int(calibration.get("guard_trips") or 0),
        parse_retries=int(calibration.get("parse_retries") or 0),
        escalation_flags=_dump_json(calibration.get("escalation_flags")),
        escalated=bool(calibration.get("escalated", False)),
        translated=bool(calibration.get("translated", False)),
        calibration_failed=bool(calibration.get("calibration_failed", False)),
    )
    db.add(run)
    db.flush()
    return run


def _dump_json(value) -> str | None:
    """Serialize a v3 component value (dict/list) for its TEXT column. None and
    empty containers store NULL; a pre-serialized string passes through."""
    if not value:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return None


def fetch_run_fingerprints(
    db: Session, source: str, song_id: int
) -> list[bytes]:
    """Return all non-superseded MinHash fingerprints for a canonical row.

    Used by the Lyrical Charger divergence guard to compare a new submission
    against prior runs for the same (title, artist). NULLs are filtered out
    — older runs predate the fingerprint column and contribute no signal.

    Unified renovation: aggregates by the atomic song (every run across its
    former duplicate rows), resolving (source, id) like compute_consensus."""
    unified = resolve_unified_song(db, source=source, song_id=song_id)
    if unified is None:
        return []
    rows = (
        db.query(CalibrationRun.lyrics_fingerprint)
        .filter(CalibrationRun.song_id == unified.id)
        .filter(CalibrationRun.superseded.is_(False))
        .filter(CalibrationRun.lyrics_fingerprint.isnot(None))
        .all()
    )
    return [r[0] for r in rows if r[0]]


def compute_consensus(db: Session, source: str, song_id: int) -> dict | None:
    """MEDIAN consensus across all live runs for a song (Calibrator v3).

    Median, not weighted mean: one outlier vote can no longer drag a verdict
    toward a tier boundary, and self-reported confidence no longer weights
    anything (it is stored per run and feeds only the escalation gate).
    Contamination stays majority-vote. Returns None if there are no usable
    runs.

    Seed hygiene: a seed run is a lazy snapshot of the song's pre-corpus
    state. When that state came from the chart_reading era (the audit's
    headline staleness predictor) and 2+ fresh runs exist, the seed is
    excluded so the stale snapshot stops voting. Any later authoritative
    method change supersedes prior runs anyway, so the song-level
    canonical_calibration_method is a stable provenance predicate.

    Unified renovation: aggregates by the atomic song. (source, song_id) is
    resolved to the unified song id (source='songs' -> id is already unified;
    a legacy pair maps via song_id_map), then every CalibrationRun for that
    unified song counts -- including runs from former duplicate rows. Signature
    kept stable for callers (e.g. the public song page)."""
    unified = resolve_unified_song(db, source=source, song_id=song_id)
    if unified is None:
        return None
    runs = (
        db.query(CalibrationRun)
        .filter(CalibrationRun.song_id == unified.id)
        .filter(CalibrationRun.charge_value.isnot(None))
        .filter(CalibrationRun.superseded.is_(False))
        .all()
    )
    if not runs:
        return None

    if (unified.canonical_calibration_method or "") == "chart_reading":
        fresh = [r for r in runs if r.triggered_by != "seed"]
        if len(fresh) >= 2:
            runs = fresh

    charges = [float(r.charge_value) for r in runs]
    consensus_charge = int(round(statistics.median(charges)))
    consensus_charge = max(-100, min(100, consensus_charge))
    consensus_color = _derive_tier(consensus_charge)
    contam_count = sum(1 for r in runs if r.contaminated)
    consensus_contaminated = contam_count > len(runs) / 2
    return {
        "run_count": len(runs),
        "charge_value": consensus_charge,
        "rubric_color": consensus_color,
        "contaminated": consensus_contaminated,
    }


# Public re-run cap: once a song has this many LIVE (non-superseded) calibration
# runs, the public Lyrical Charger stops running it -- the reading is considered
# settled. Further runs are admin/terminal-only (only tier=='public' is gated;
# service and terminal callers bypass). Counts NON-SUPERSEDED runs, so a
# rubric_update or admin recalibration (which supersedes the prior runs) resets
# the budget and reopens the song to the public -- "the measuring stick moved,
# read it fresh." Superseded runs stay in the ledger / Runs view but don't count
# toward the cap.
PUBLIC_RUN_CAP = 10


def supersede_live_runs(db: Session, song_id: int, reason: str) -> int:
    """Mark every LIVE run on a song superseded. Returns how many were flipped.

    The recalibration contract, factored out of routers/recalibrations.py so the
    write lane can use it too: a fresh verdict RETIRES the prior ones rather than
    joining them. Three things depend on that. `live_run_count` (the public
    re-run cap) keeps counting one current verdict instead of accumulating every
    operator pass; `compute_consensus` stays out of it, since it only fires at
    run_count >= 2 and must never average a fresh read against stale runs; and
    the superseded rows stay in the table, so the song page still renders the
    full history with each retired run flagged.

    Caller commits."""
    from datetime import datetime as _dt
    now = _dt.utcnow()
    prior = (
        db.query(CalibrationRun)
        .filter(CalibrationRun.song_id == song_id)
        .filter(CalibrationRun.superseded.is_(False))
        .all()
    )
    for r in prior:
        r.superseded = True
        r.superseded_reason = reason
        r.superseded_at = now
    return len(prior)


def live_run_count(db: Session, song_id: int) -> int:
    """Non-superseded calibration runs for a song -- the live corpus that the
    public run cap counts against. Superseded runs (post rubric_update /
    recalibration) are excluded so the cap reopens after a deliberate reset."""
    return (
        db.query(func.count(CalibrationRun.id))
        .filter(CalibrationRun.song_id == song_id)
        .filter(CalibrationRun.superseded.is_(False))
        .scalar()
    ) or 0


def apply_consensus_to_song(
    db: Session,
    *,
    source: str,
    song,
    consensus: dict,
    title: str | None,
    artist: str | None,
) -> bool:
    """Update the canonical song row with consensus values. If the tier color
    flips, log a song_recalibrations entry of type 'consensus_drift' so the
    shift is publicly audited. Returns True if anything was changed.
    """
    before_charge = getattr(song, "charge_value", None)
    before_color = getattr(song, "rubric_color", None)

    changed = False
    new_charge = consensus["charge_value"]
    new_color = consensus["rubric_color"]
    new_contam = consensus["contaminated"]

    if before_charge != new_charge:
        song.charge_value = new_charge
        changed = True
    if (before_color or "") != new_color:
        song.rubric_color = new_color
        changed = True
    if hasattr(song, "contaminated"):
        if bool(getattr(song, "contaminated", False)) != bool(new_contam):
            song.contaminated = bool(new_contam)
            changed = True

    tier_flip = (before_color or "") != new_color and before_charge is not None
    if tier_flip:
        recal = SongRecalibration(
            lens="standard",
            song_id=song.id,
            pipeline="consensus_drift",
            trigger_ref_id=None,
            before_charge=before_charge,
            before_color=before_color,
            before_summary=getattr(song, "charge_summary", None),
            after_charge=new_charge,
            after_color=new_color,
            ai_rationale=None,
            public_summary=(
                f"Consensus across {consensus['run_count']} agent runs shifted "
                f"this song from {before_color or 'uncalibrated'} "
                f"({before_charge if before_charge is not None else '—'}) "
                f"to {new_color} ({new_charge})."
            ),
            internal_notes=None,
            vibe_snapshot=_vibe_snapshot_json(db, source, song.id),
        )
        db.add(recal)

    return changed


def record_and_reconcile(
    db: Session,
    *,
    title: str | None,
    artist: str | None,
    calibration: dict,
    triggered_by: str,
    lyrics_hash: str | None = None,
    lyrics_fingerprint: bytes | None = None,
    agent_model: str | None = None,
    direct_song_source: str | None = None,
    direct_song_id: int | None = None,
    is_new_row: bool = False,
    lyrics: str | None = None,
    allow_prose_generation: bool = True,
) -> dict:
    """Full consensus flow: log the run, seed prior state if needed, compute
    consensus, update the canonical row, audit tier flips.

    `direct_song_source` / `direct_song_id` let callers point straight at the
    row they just wrote (LC submit → submitted_songs, compass agent →
    compass_songs). When `is_new_row=True`, we skip the seed step — the row's
    current state IS this very submission, so seeding would log a duplicate
    of the user's run and inflate the consensus count.

    If no direct pointer is given, we match by (title, artist) — exact string
    first, then via song_artists for cross-credit matching.

    Returns {"consensus": {...}, "run": {...}, "user_run": {...}}.
    """
    # Resolve the atomic unified song. A direct (source, id) pointer wins
    # (source='songs' is the unified id; a legacy pair maps via song_id_map);
    # otherwise match by (title, artist) / credit path.
    song = None
    if direct_song_source and direct_song_id:
        song = resolve_unified_song(
            db, source=direct_song_source, song_id=direct_song_id,
            title=title, artist=artist,
        )
    if song is None:
        song = resolve_unified_song(db, title=title, artist=artist)

    source = "songs" if song is not None else None

    # Only seed when we're reconciling against a PRE-EXISTING row. A row that
    # was just created by this very submission has no prior history to seed
    # — the current calibration IS the user's run.
    if song is not None and not is_new_row:
        _seed_initial_run_if_missing("songs", song, db)

    run = log_run(
        db,
        title=title,
        artist=artist,
        calibration=calibration,
        triggered_by=triggered_by,
        song_id=song.id if song else None,
        lyrics_hash=lyrics_hash,
        lyrics_fingerprint=lyrics_fingerprint,
        agent_model=agent_model,
        lyrics=lyrics,
    )

    consensus = None
    if song and source:
        # Snapshot before consensus might mutate so we know whether prose
        # needs to regenerate.
        prior_color = getattr(song, "rubric_color", None)
        prior_summary = getattr(song, "charge_summary", None)

        consensus = compute_consensus(db, source, song.id)
        if consensus and consensus["run_count"] >= 2:
            apply_consensus_to_song(
                db, source=source, song=song,
                consensus=consensus, title=title, artist=artist,
            )

        # Generate per-song effects prose when the canonical has tier +
        # summary AND either (a) prose is missing, or (b) the tier or
        # summary shifted in this reconcile pass. Fails soft — on error,
        # leave the column as-is and the page falls back to tier-generic
        # copy.
        cur_color = getattr(song, "rubric_color", None)
        cur_summary = getattr(song, "charge_summary", None)
        tier_or_summary_changed = (prior_color != cur_color) or (prior_summary != cur_summary)
        prose_missing = not getattr(song, "listener_effects_prose", None)
        # allow_prose_generation is False for terminal (Claude-Code-supplied)
        # calibrations: Claude Code IS the model and supplies the prose, so the
        # server must NEVER call Anthropic here. When it forgets, the column stays
        # NULL (page falls back to tier-generic) rather than drawing on the
        # public-traffic budget. See feedback_rc_no_api_in_terminal.
        if allow_prose_generation and cur_color and cur_summary and (prose_missing or tier_or_summary_changed):
            try:
                from app.services.listener_effects_prose import generate_listener_effects_prose
                prose = generate_listener_effects_prose(
                    title=getattr(song, "title", None) or title or "",
                    artist=getattr(song, "artist", None) or artist or "",
                    rubric_color=cur_color,
                    charge_value=getattr(song, "charge_value", None),
                    charge_summary=cur_summary,
                    contaminated=bool(getattr(song, "contaminated", False)),
                    contamination_note=getattr(song, "contamination_note", None),
                )
                if prose:
                    song.listener_effects_prose = prose
            except Exception:
                logger.exception("listener_effects_prose hook failed for %s/%s", source, song.id)

        # Societal effects prose hook. Fires when (a) the listener prose just
        # generated/regenerated AND (b) the row has ether tags to ground the
        # societal read. On first calibration topics are still NULL (the
        # ether tagger runs later in compass_agent), so this hook only
        # triggers on reconcile passes — first-row generation happens in
        # compass_agent right after the tagger sets topics.
        soc_missing = not getattr(song, "societal_effects_prose", None)
        has_topics = bool(getattr(song, "topics", None))
        if (allow_prose_generation and cur_color and cur_summary and has_topics
                and (soc_missing or tier_or_summary_changed)):
            try:
                from app.services.societal_effects_prose import generate_societal_effects_prose
                soc = generate_societal_effects_prose(
                    title=getattr(song, "title", None) or title or "",
                    artist=getattr(song, "artist", None) or artist or "",
                    rubric_color=cur_color,
                    charge_value=getattr(song, "charge_value", None),
                    charge_summary=cur_summary,
                    contaminated=bool(getattr(song, "contaminated", False)),
                    contamination_note=getattr(song, "contamination_note", None),
                    deadpan_line=getattr(song, "deadpan_line", None),
                    topics=getattr(song, "topics", None),
                    listener_effects_prose=getattr(song, "listener_effects_prose", None),
                )
                if soc:
                    song.societal_effects_prose = soc.prose
                    song.societal_prose_generated_at = soc.generated_at
                    song.societal_prose_model = soc.model
            except Exception:
                logger.exception("societal_effects_prose hook failed for %s/%s", source, song.id)

        # Psyche Facts synthesis hook. Fires when the row has tier + summary + the
        # listener prose (the synthesis substrate) AND the panel is incomplete or the
        # tier/summary shifted. SYNTHESIS from the already-generated fields
        # (lyric-free), so it never touches raw lyrics. allow_prose_generation gates
        # it to the public path exactly like the two prose hooks; the terminal path
        # supplies the whole panel via calibrate_song.py --psyche-facts-file (which
        # carries effects_pl[] in the same object) and passes
        # allow_prose_generation=False, so this never fires there. Fails soft.
        #
        # THE PANEL IS ONE THING: the prescription bundle (`psyche_facts`) and the
        # per-listen effects tags (`effects_pl`, the tag axis this family reserves,
        # migration 140) are generated in ONE call and written TOGETHER below.
        # Before 2026-07-14 only the bundle had a generator, so effects_pl was NULL
        # corpus-wide on the public path and read as optional on the terminal path.
        # Hence `pf_missing` covers BOTH columns: a row holding one without the
        # other is incomplete and re-fires.
        pf_missing = not (getattr(song, "psyche_facts", None)
                          and getattr(song, "effects_pl", None))
        if (allow_prose_generation and cur_color and cur_summary
                and getattr(song, "listener_effects_prose", None)
                and (pf_missing or tier_or_summary_changed)):
            try:
                from app.services.psyche_facts import generate_psyche_facts
                pf_result = generate_psyche_facts(
                    title=getattr(song, "title", None) or title or "",
                    artist=getattr(song, "artist", None) or artist or "",
                    rubric_color=cur_color,
                    charge_value=getattr(song, "charge_value", None),
                    charge_summary=cur_summary,
                    contaminated=bool(getattr(song, "contaminated", False)),
                    contamination_note=getattr(song, "contamination_note", None),
                    deadpan_line=getattr(song, "deadpan_line", None),
                    topics=getattr(song, "topics", None),
                    listener_effects_prose=getattr(song, "listener_effects_prose", None),
                    societal_effects_prose=getattr(song, "societal_effects_prose", None),
                )
                if pf_result:
                    song.psyche_facts = json.dumps(pf_result.bundle)
                    # Written in the same breath as the bundle. An empty list is a
                    # legal synthesis outcome, so only overwrite when the call
                    # actually produced tags -- never null out an existing set.
                    if pf_result.effects_pl:
                        song.effects_pl = json.dumps(pf_result.effects_pl)
            except Exception:
                logger.exception("psyche_facts hook failed for %s/%s", source, song.id)

        # Push the finalized calibration to chadlewine so it serves badges from
        # local state instead of calling RC live. Fire-and-forget + fail-soft +
        # ships dark (no-op unless CHADLEWINE_WEBHOOK_URL/SECRET are set); the
        # payload is built synchronously off the in-memory `song`, so the daemon
        # thread never touches this session.
        try:
            from app.services.chadlewine_webhook import push_song_classification
            push_song_classification(song)
        except Exception:
            logger.warning("chadlewine push hook failed (non-fatal)", exc_info=True)

    return {
        "run_id": run.id,
        "user_run": {
            "rubric_color": calibration.get("rubric_color"),
            "charge_value": calibration.get("charge_value"),
            "charge_summary": calibration.get("charge_summary"),
            "contaminated": bool(calibration.get("contaminated", False)),
            "confidence": calibration.get("confidence"),
        },
        "consensus": consensus,
        "song_source": source,
        "song_id": song.id if song else None,
    }
