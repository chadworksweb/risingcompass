"""Calibrator v3 feedback organ -- the social-calibration divergence report.

The compass calibrates socially over time: audience reactions FLAG divergence,
they never move the needle. This report finds songs where the crowd's signals
systematically oppose the stored verdict and NOMINATES them for a human-ruled
re-read. Hard line: read-only -- it never writes to songs or runs a
calibration; the nomination list is the entire output.

Two sources, both already collected by the product:
  - audience_vibe_needles: pushed needles whose position opposes the stored
    charge's sign, or sits far from its magnitude, with enough volume to mean
    something.
  - misread_submissions: open ("the agent got this wrong") reports clustering
    on the same song.

Built with v3 and live-empty by design: there is no traffic yet, so the report
returns zero rows today. The moment an audience exists, the loop is already
running. No Anthropic calls -- pure SQL over existing tables. Spec:
RISING-COMPASS-CALIBRATOR-V3.md section 2.5.
"""

import logging

from sqlalchemy import func

from app.database import SessionLocal
from app.models import AudienceVibeNeedle, MisreadSubmission, Song
from app.services.agents.warehouse import start_run, finish_run

logger = logging.getLogger(__name__)

# This report IS the agent "Surveyor 001" in the admin mini-warehouse.
AGENT_ID = "surveyor-001"

# A needle with fewer pushes than this is one person's opinion, not a signal.
MIN_VIBE_PUSHES = 3
# Magnitude pressure: same sign but the crowd sits at least this far from the
# stored charge (half the scale) also counts as opposition.
MAGNITUDE_GAP = 50
# Misread reports clustering on one song. Two strangers reporting the same
# song is a pattern; one is noise.
MIN_MISREAD_CLUSTER = 2
MAX_ROWS = 100


def _vibe_nominations(db) -> dict[int, dict]:
    """Songs whose pushed vibe needle opposes the stored charge."""
    rows = (
        db.query(AudienceVibeNeedle, Song)
        .join(Song, Song.id == AudienceVibeNeedle.song_id)
        .filter(Song.charge_value.isnot(None))
        .filter(
            (AudienceVibeNeedle.pushes_up_total
             + AudienceVibeNeedle.pushes_down_total) >= MIN_VIBE_PUSHES
        )
        .all()
    )
    out: dict[int, dict] = {}
    for needle, song in rows:
        charge = int(song.charge_value)
        vibe = int(needle.current_value)
        volume = int(needle.pushes_up_total + needle.pushes_down_total)
        sign_opposed = (charge > 0 > vibe) or (charge < 0 < vibe)
        gap = abs(vibe - charge)
        if not sign_opposed and gap < MAGNITUDE_GAP:
            continue
        out[song.id] = {
            "song_id": song.id,
            "title": song.title,
            "artist": song.artist,
            "stored_charge": charge,
            "stored_tier": song.rubric_color,
            "vibe_value": vibe,
            "pushes_up": int(needle.pushes_up_total),
            "pushes_down": int(needle.pushes_down_total),
            "misread_count": 0,
            "signals": ["vibe_sign_opposed" if sign_opposed else "vibe_magnitude_gap"],
            "rank_score": gap * volume,
        }
    return out


def _misread_nominations(db) -> list[dict]:
    """Open misread reports clustering on the same (title, artist). The table
    predates the unified song FK, so clusters key on the text identity and
    resolve to the unified song afterward (unresolved clusters still report)."""
    clusters = (
        db.query(
            func.lower(MisreadSubmission.song_title).label("t"),
            func.lower(MisreadSubmission.song_artist).label("a"),
            func.max(MisreadSubmission.song_title).label("title"),
            func.max(MisreadSubmission.song_artist).label("artist"),
            func.count(MisreadSubmission.id).label("n"),
        )
        .filter(MisreadSubmission.status == "pending")
        .filter(MisreadSubmission.report_type == "misread")
        .group_by("t", "a")
        .having(func.count(MisreadSubmission.id) >= MIN_MISREAD_CLUSTER)
        .all()
    )
    out = []
    for c in clusters:
        from app.services.calibration_corpus import resolve_unified_song
        song = resolve_unified_song(db, title=c.title, artist=c.artist)
        out.append({
            "song_id": song.id if song else None,
            "title": c.title,
            "artist": c.artist,
            "stored_charge": int(song.charge_value) if song and song.charge_value is not None else None,
            "stored_tier": song.rubric_color if song else None,
            "vibe_value": None,
            "pushes_up": 0,
            "pushes_down": 0,
            "misread_count": int(c.n),
            "signals": ["misread_cluster"],
            "rank_score": int(c.n) * 25,
        })
    return out


def run_divergence_report(trigger: str = "cron") -> dict:
    """Run one report pass. Returns {scanned, nominated, nominations}.

    Records the run in the agent mini-warehouse (Surveyor 001) exactly like
    the clutter sweep: fail-soft tracking, a crash recorded as a failed run
    AND re-raised so Faultline + the cron alert still fire."""
    run_id = start_run(AGENT_ID, trigger)
    scanned = 0
    nominated = 0
    try:
        summary = _run_report_inner()
        scanned = summary.get("scanned", 0)
        nominated = summary.get("nominated", 0)
        finish_run(run_id, status="ok", scanned=scanned, flagged=nominated)
        return summary
    except Exception as exc:
        finish_run(run_id, status="error", scanned=scanned, flagged=nominated,
                   error=f"{type(exc).__name__}: {exc}"[:1000])
        raise


def _run_report_inner() -> dict:
    db = SessionLocal()
    try:
        vibe = _vibe_nominations(db)
        misreads = _misread_nominations(db)

        # Merge: a song flagged by both sources is one nomination with both
        # signals and the combined rank.
        merged: dict = dict(vibe)
        unresolved: list[dict] = []
        for m in misreads:
            if m["song_id"] is None:
                unresolved.append(m)
                continue
            if m["song_id"] in merged:
                row = merged[m["song_id"]]
                row["misread_count"] = m["misread_count"]
                row["signals"].append("misread_cluster")
                row["rank_score"] += m["rank_score"]
            else:
                merged[m["song_id"]] = m

        nominations = sorted(
            list(merged.values()) + unresolved,
            key=lambda r: r["rank_score"], reverse=True,
        )[:MAX_ROWS]

        scanned = (
            db.query(func.count(AudienceVibeNeedle.id)).scalar() or 0
        ) + (
            db.query(func.count(MisreadSubmission.id))
            .filter(MisreadSubmission.status == "pending").scalar() or 0
        )
        logger.info("divergence_report: scanned=%d nominated=%d",
                    scanned, len(nominations))
        return {
            "scanned": int(scanned),
            "nominated": len(nominations),
            "nominations": nominations,
        }
    finally:
        db.close()
