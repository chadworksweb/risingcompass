"""Calibration corpus + consensus engine.

Every agent run — wherever it fires — lands in calibration_runs. When a run
targets an existing song, the canonical row drifts toward the
confidence-weighted mean of all its runs. Tier flips from consensus drift
are logged as song_recalibrations entries so the public audit trail stays
honest.

This is two things at once: a user-facing consensus ("this song was
calibrated 8 times, consensus is orange -34") and a training corpus for
later model work (input = lyrics hash + song identity, output = full
classification).

Lyrics themselves are never stored — only a SHA-256 hash for dedupe /
variance awareness.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    CalibrationRun, CompassSong, LibrarySong, SubmittedSong, StreamSong,
    SongRecalibration, Artist, SongArtist,
)

logger = logging.getLogger(__name__)


_SONG_TABLES = [
    ("compass", CompassSong),
    ("library", LibrarySong),
    ("submitted", SubmittedSong),
    ("stream", StreamSong),
]


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


def _derive_tier(charge: int) -> str:
    """Map a charge value back to a rubric color. Matches derive_tier in
    artist_utils but avoids the cross-module import cycle.
    """
    if charge >= 75: return "violet"
    if charge >= 25: return "blue"
    if charge >= -24: return "green"
    if charge >= -74: return "orange"
    return "red"


def find_canonical_song(title: str, artist: str, db: Session) -> tuple[str, object] | None:
    """Find the canonical song row for (title, credit_string).

    Two-pass match. Priority: compass > library > submitted > stream.
      1. Exact case-insensitive match on the full artist credit string.
      2. Fallback: split the submitted credit into individual artists, look
         them up in the artists table, and find a song with the same title
         that has ANY of those artists credited via song_artists. This
         catches "Runway by Doechii" matching a prior "Runway by Lady Gaga
         & Doechii" entry — same song, different credit ordering.
    """
    if not title or not artist:
        return None
    t_lower = title.strip().lower()
    a_lower = artist.strip().lower()

    for source, Model in _SONG_TABLES:
        if not hasattr(Model, "title") or not hasattr(Model, "artist"):
            continue
        row = (
            db.query(Model)
            .filter(func.lower(Model.title) == t_lower)
            .filter(func.lower(Model.artist) == a_lower)
            .first()
        )
        if row:
            return source, row

    # Fallback via song_artists — catches cross-credit matches.
    from app.services.artist_linker import parse_artist_string
    entries = parse_artist_string(artist)
    artist_ids: list[int] = []
    for e in entries:
        name = (e.get("name") or "").strip()
        if not name:
            continue
        a_row = (
            db.query(Artist)
            .filter(func.lower(Artist.name) == name.lower())
            .first()
        )
        if a_row:
            artist_ids.append(a_row.id)
    if not artist_ids:
        return None

    for source, Model in _SONG_TABLES:
        if not hasattr(Model, "title"):
            continue
        candidate_ids = {
            sid for (sid,) in (
                db.query(SongArtist.song_id)
                .filter(SongArtist.song_source == source)
                .filter(SongArtist.artist_id.in_(artist_ids))
                .distinct()
                .all()
            )
        }
        if not candidate_ids:
            continue
        row = (
            db.query(Model)
            .filter(Model.id.in_(candidate_ids))
            .filter(func.lower(Model.title) == t_lower)
            .first()
        )
        if row:
            return source, row
    return None


# Back-compat alias — older code paths may still import the private name.
_find_canonical_song = find_canonical_song


def _seed_initial_run_if_missing(source: str, song, db: Session):
    """Lazy backfill: if a song has no calibration_runs history yet but is
    already calibrated, log its current state as the initial run so consensus
    starts with proper context.
    """
    existing_count = (
        db.query(func.count(CalibrationRun.id))
        .filter(CalibrationRun.song_source == source)
        .filter(CalibrationRun.song_id == song.id)
        .scalar()
    )
    if existing_count > 0:
        return
    if getattr(song, "charge_value", None) is None:
        # Song is uncalibrated (or was reset) — nothing to seed
        return
    seed = CalibrationRun(
        song_source=source,
        song_id=song.id,
        title=getattr(song, "title", None),
        artist=getattr(song, "artist", None),
        rubric_color=getattr(song, "rubric_color", None) or None,
        charge_value=getattr(song, "charge_value", None),
        charge_summary=getattr(song, "charge_summary", None),
        contaminated=bool(getattr(song, "contaminated", False)),
        contamination_note=getattr(song, "contamination_note", None),
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
    lyrics_hash: str | None = None,
    agent_model: str | None = None,
) -> CalibrationRun:
    """Record one agent run. Always writes. The caller has already committed
    the song row (or decided no song row is appropriate).
    """
    run = CalibrationRun(
        song_source=song_source,
        song_id=song_id,
        title=title,
        artist=artist,
        rubric_color=calibration.get("rubric_color"),
        charge_value=calibration.get("charge_value"),
        charge_summary=calibration.get("charge_summary"),
        contaminated=bool(calibration.get("contaminated", False)),
        contamination_note=calibration.get("contamination_note"),
        confidence=calibration.get("confidence"),
        agent_model=agent_model,
        triggered_by=triggered_by,
        lyrics_hash=lyrics_hash,
    )
    db.add(run)
    db.flush()
    return run


def compute_consensus(db: Session, source: str, song_id: int) -> dict | None:
    """Weighted-mean consensus across all runs for a song. Weighted by
    confidence (defaults to 0.5 when the run didn't report one). Returns
    None if there are no usable runs.
    """
    runs = (
        db.query(CalibrationRun)
        .filter(CalibrationRun.song_source == source)
        .filter(CalibrationRun.song_id == song_id)
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
            song_source=source,
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
    canonical = None
    if direct_song_source and direct_song_id:
        for source, Model in _SONG_TABLES:
            if source == direct_song_source:
                row = db.query(Model).get(direct_song_id)
                if row:
                    canonical = (source, row)
                break
    if canonical is None:
        canonical = find_canonical_song(title, artist, db)

    source = canonical[0] if canonical else None
    song = canonical[1] if canonical else None

    # Only seed when we're reconciling against a PRE-EXISTING row. A row that
    # was just created by this very submission has no prior history to seed
    # — the current calibration IS the user's run.
    if canonical and not is_new_row:
        _seed_initial_run_if_missing(source, song, db)

    run = log_run(
        db,
        title=title,
        artist=artist,
        calibration=calibration,
        triggered_by=triggered_by,
        song_source=source,
        song_id=song.id if song else None,
        lyrics_hash=lyrics_hash,
        agent_model=agent_model,
    )

    consensus = None
    if song and source:
        consensus = compute_consensus(db, source, song.id)
        if consensus and consensus["run_count"] >= 2:
            apply_consensus_to_song(
                db, source=source, song=song,
                consensus=consensus, title=title, artist=artist,
            )

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
