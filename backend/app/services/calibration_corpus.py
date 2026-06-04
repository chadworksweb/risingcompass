"""Calibration corpus + consensus engine.

Every agent run — wherever it fires — lands in calibration_runs. When a run
targets an existing song, the canonical row drifts toward the
confidence-weighted mean of all its runs. Tier flips from consensus drift
are logged as song_recalibrations entries so the public audit trail stays
honest.

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
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models import (
    CalibrationRun, CompassSong, LibrarySong, SubmittedSong, StreamSong,
    SongRecalibration, Artist, SongArtist, Song,
)
from app.services.song_identity import compute_canonical_key

logger = logging.getLogger(__name__)


_SONG_TABLES = [
    ("compass", CompassSong),
    ("library", LibrarySong),
    ("submitted", SubmittedSong),
    ("stream", StreamSong),
]

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
        key = compute_canonical_key(title, artist)
        song = db.query(Song).filter(Song.canonical_key == key).first()
        if song:
            return song
        return _find_song_via_credits(db, title, artist)
    return None


def _find_song_via_credits(db: Session, title: str, artist: str) -> Song | None:
    """Credit-path fallback: split the credit string, look the artists up, and
    find a unified song with the same title carrying ANY of them via
    song_artists.unified_song_id. Catches reordered/abbreviated credits."""
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
            db.query(SongArtist.unified_song_id)
            .filter(SongArtist.artist_id.in_(artist_ids))
            .filter(SongArtist.unified_song_id.isnot(None))
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


def _derive_tier(charge: int) -> str:
    """Map a charge value back to a rubric color. Matches derive_tier in
    artist_utils but avoids the cross-module import cycle.
    """
    if charge >= 75: return "violet"
    if charge >= 25: return "blue"
    if charge >= -24: return "green"
    if charge >= -74: return "orange"
    return "red"


def find_same_table_song(
    db: Session, model, title: str, artist: str,
) -> object | None:
    """Case-insensitive (title, artist) lookup inside a single song table.

    Used by get_or_create_song as the dedupe guard at every ingestion point.
    Matches the normalization semantics of find_canonical_song (trim + lower,
    no aggressive punctuation stripping). For fuzzier match semantics a caller
    should use find_canonical_song across all four tables instead.
    """
    if not title or not artist:
        return None
    return (
        db.query(model)
        .filter(func.lower(model.title) == title.strip().lower())
        .filter(func.lower(model.artist) == artist.strip().lower())
        .order_by(model.id.asc())
        .first()
    )


def get_or_create_song(
    db: Session, model, *, title: str, artist: str, **fields,
) -> tuple[object, bool]:
    """Idempotent insert: return (row, created) where created=False means a
    pre-existing row with the same (title, artist) was returned instead.

    Central dedup guard — every ingestion path (LC submit, backfill,
    stream POST, admin manual create) routes through here so the same
    (title, artist) inside a single table can never produce two rows.
    Cross-table canonical promotion is a different concern, handled by
    find_canonical_song + the compass→library→submitted→stream precedence
    in badge.lookup.

    Handles the race where two concurrent requests both pass the initial
    find — the second one hits the DB's UNIQUE(lower(title), lower(artist))
    index (migration 037) and raises IntegrityError. We rollback and re-find
    so the caller still gets back the winning row with created=False.
    """
    from sqlalchemy.exc import IntegrityError

    existing = find_same_table_song(db, model, title, artist)
    if existing is not None:
        return existing, False
    row = model(title=title, artist=artist, **fields)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        winner = find_same_table_song(db, model, title, artist)
        if winner is None:
            raise
        return winner, False
    return row, True


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
        .filter(CalibrationRun.unified_song_id == unified.id)
        .scalar()
    )
    if existing_count > 0:
        return
    if getattr(song, "charge_value", None) is None:
        # Song is uncalibrated (or was reset) — nothing to seed
        return
    seed = CalibrationRun(
        song_source="songs",
        song_id=unified.id,
        unified_song_id=unified.id,
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


def log_run(
    db: Session,
    *,
    title: str | None,
    artist: str | None,
    calibration: dict,
    triggered_by: str,
    song_source: str | None = None,
    song_id: int | None = None,
    unified_song_id: int | None = None,
    lyrics_hash: str | None = None,
    lyrics_fingerprint: bytes | None = None,
    agent_model: str | None = None,
) -> CalibrationRun:
    """Record one agent run. Always writes. The caller has already committed
    the song row (or decided no song row is appropriate). `unified_song_id`
    keys the run to the atomic song for consensus aggregation."""
    run = CalibrationRun(
        song_source=song_source,
        song_id=song_id,
        unified_song_id=unified_song_id,
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
    )
    db.add(run)
    db.flush()
    return run


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
        .filter(CalibrationRun.unified_song_id == unified.id)
        .filter(CalibrationRun.superseded.is_(False))
        .filter(CalibrationRun.lyrics_fingerprint.isnot(None))
        .all()
    )
    return [r[0] for r in rows if r[0]]


def compute_consensus(db: Session, source: str, song_id: int) -> dict | None:
    """Weighted-mean consensus across all runs for a song. Weighted by
    confidence (defaults to 0.5 when the run didn't report one). Returns
    None if there are no usable runs.

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
        .filter(CalibrationRun.unified_song_id == unified.id)
        .filter(CalibrationRun.charge_value.isnot(None))
        .filter(CalibrationRun.superseded.is_(False))
        .all()
    )
    if not runs:
        return None
    weight_sum = 0.0
    weighted_charge = 0.0
    contam_count = 0
    for r in runs:
        w = float(r.confidence) if r.confidence is not None else 0.5
        if w <= 0:
            w = 0.1
        weight_sum += w
        weighted_charge += w * float(r.charge_value)
        if r.contaminated:
            contam_count += 1
    if weight_sum == 0:
        return None
    consensus_charge = round(weighted_charge / weight_sum)
    consensus_charge = max(-100, min(100, consensus_charge))
    consensus_color = _derive_tier(consensus_charge)
    consensus_contaminated = contam_count > len(runs) / 2
    return {
        "run_count": len(runs),
        "charge_value": consensus_charge,
        "rubric_color": consensus_color,
        "contaminated": consensus_contaminated,
    }


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
            song_source="songs",
            song_id=song.id,
            unified_song_id=song.id,
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
        song_source=source,
        song_id=song.id if song else None,
        unified_song_id=song.id if song else None,
        lyrics_hash=lyrics_hash,
        lyrics_fingerprint=lyrics_fingerprint,
        agent_model=agent_model,
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
        prose_missing = not getattr(song, "effects_prose", None)
        if cur_color and cur_summary and (prose_missing or tier_or_summary_changed):
            try:
                from app.services.effects_prose import generate_effects_prose
                prose = generate_effects_prose(
                    title=getattr(song, "title", None) or title or "",
                    artist=getattr(song, "artist", None) or artist or "",
                    rubric_color=cur_color,
                    charge_value=getattr(song, "charge_value", None),
                    charge_summary=cur_summary,
                    contaminated=bool(getattr(song, "contaminated", False)),
                    contamination_note=getattr(song, "contamination_note", None),
                )
                if prose:
                    song.effects_prose = prose
            except Exception:
                logger.exception("effects_prose hook failed for %s/%s", source, song.id)

        # Societal effects prose hook. Fires when (a) the listener prose just
        # generated/regenerated AND (b) the row has ether tags to ground the
        # societal read. On first calibration topics are still NULL (the
        # ether tagger runs later in compass_agent), so this hook only
        # triggers on reconcile passes — first-row generation happens in
        # compass_agent right after the tagger sets topics.
        soc_missing = not getattr(song, "societal_effects_prose", None)
        has_topics = bool(getattr(song, "topics", None))
        if (cur_color and cur_summary and has_topics
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
                    effects_prose=getattr(song, "effects_prose", None),
                )
                if soc:
                    song.societal_effects_prose = soc.prose
                    song.societal_prose_generated_at = soc.generated_at
                    song.societal_prose_model = soc.model
            except Exception:
                logger.exception("societal_effects_prose hook failed for %s/%s", source, song.id)

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
