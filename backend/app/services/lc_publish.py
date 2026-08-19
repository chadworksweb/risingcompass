"""Hold a Lyrical Charger reading, then publish it when the reader accepts.

THE SPLIT. A public reading used to be produced and committed in one breath. It
is now two moments: DELIVERY (the reader sees it) and PUBLICATION (it enters the
Library). Everything between them is the reader's window to say the reading is
wrong and get one re-read, which is the rung that was missing between "correct"
and "file a report and wait for an admin".

WHAT MOVED AND WHAT DID NOT. Four writes are deferred to publication, because
each one is the reading entering a public surface:

    store_calibrated_song   the songs row itself
    record_and_reconcile    the calibration_runs ledger + consensus
    _get_or_create_slug     the public /songs/{slug} URL
    _record_user_calibration the signed-in reader's attribution

The CREDIT CHARGE deliberately did NOT move, and neither did the success
telemetry or the clutter flag. Those belong to delivery: the reader received a
reading, and they received it whether or not they go on to contest it.
Deferring the charge would hand a free reading to anyone who clicks contest.

WHY NOT PUBLISH-THEN-SUPERSEDE. Because a superseded run is deliberately still
visible -- the song page runs timeline renders it as history, and _most_run
counts it toward "most calibrated". Both are correct for a rubric_update
supersede and both would leak a contested first reading into public view. A
reading that never published leaks nowhere, which is what makes "as if the first
one never existed" a true statement rather than a nearly-true one.

THE LYRICS ARE NOT HERE. "Lyrics text is never stored, anywhere" is a hard legal
constraint (LC-LYRICS-GUARDS.md) and the hold row honours it exactly like every
other surface: fingerprint yes, words no. Two consequences run through this
file:

  1. A contested re-read needs the lyrics again and gets them by having the
     READER PASTE THEM AGAIN. charger.js clears the input the moment a reading
     lands, so there is nothing cached to resend even in principle.
  2. The stored agent argument has to be scrubbed HERE, at hold time, while the
     lyrics are still in memory. By publication they are gone, and log_run's
     guard fails closed without them -- it would drop the argument on every
     public run, forever. `hold_read` therefore runs the scrub itself and
     stores only its output, and publication passes `pre_scrubbed=True`, an
     assertion this module makes true by construction rather than by trust.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models import LcPrepublishRead

logger = logging.getLogger(__name__)

# How long a held reading waits before the sweep publishes it.
#
# SILENCE MEANS ACCEPTED, and it has to, because most readers click nothing.
# A window that expires into DISCARD would quietly throw away the majority of
# the Charger's intake -- the feature would read as working while the Library
# starved. So the sweep publishes, and only an explicit contest interrupts it.
#
# Thirty minutes is long enough that a reader who wanders off mid-decision and
# comes back still finds their reading contestable, and short enough that the
# Library is never meaningfully behind what readers have seen.
HOLD_TTL = timedelta(minutes=30)

# Terminal statuses. A row in one of these is never swept and never re-opened.
# 'publishing' is deliberately NOT here and not terminal either: it is the
# transient claim one caller holds while it runs the four writes, and the sweep
# takes it back after PUBLISHING_STALE if that caller never finished.
CLOSED_STATUSES = ("published", "contested", "declined", "discarded")

# What `close_read` may set, which is every terminal status EXCEPT published.
# Publication is not a status change, it is four writes plus a song id, and
# `publish_read` is the only thing that can make the row's claim true. Letting
# close_read stamp 'published' would mark a reading as being in the Library
# while nothing was ever written there, and the sweep would then skip it
# forever: a reading silently lost, looking exactly like a successful one.
CLOSE_STATUSES = ("contested", "declined", "discarded")

# How long a row may sit in the transient 'publishing' status before the sweep
# takes it back. Publication is sub-second database work with no model call, so
# anything still claimed after this window is a process that died mid-publish,
# not one still working. Generous by an order of magnitude on purpose: taking a
# row back early would run the four writes twice, which is the exact duplicate
# the claim exists to prevent.
PUBLISHING_STALE = timedelta(minutes=15)


def _now():
    return datetime.now(timezone.utc)


def _claim_for_publish(db: Session, row: LcPrepublishRead) -> bool:
    """Take exclusive ownership of one held row. True when this caller won it.

    THE CHECK HAS TO BE THE WRITE. `publish_read` used to read `row.status` in
    Python and act on it, which is not a decision at all when two callers are
    live -- and two callers are the normal case at the 30-minute mark, when a
    reader clicking "Looks right" meets the sweep arriving for the same row.
    Both would see 'held', both would run the four writes, and the run ledger
    would take a second row for one reading. `store_calibrated_song` upserts by
    canonical_key so the song survives, but that duplicate run counts toward
    _most_run -- which is precisely the leak the whole hold design exists to
    prevent, arriving by a different door.

    One UPDATE, guarded on the status it expects, decides it: exactly one caller
    changes a row and the other is told it lost.
    """
    claimed = (
        db.query(LcPrepublishRead)
        .filter(LcPrepublishRead.id == row.id)
        .filter(LcPrepublishRead.status == "held")
        .update({"status": "publishing", "updated_at": _now()},
                synchronize_session=False)
    )
    db.commit()
    db.refresh(row)
    return bool(claimed)


def hold_read(
    db: Session,
    *,
    job_token: str,
    title: str,
    artist: str,
    source: str | None,
    calibration: dict,
    result_payload: dict | None,
    lyrics: str,
    lyrics_fingerprint: bytes | None,
    user_id: int | None = None,
    device_id: str | None = None,
    ip_address: str | None = None,
    contest_of_id: int | None = None,
    contest_axis: str | None = None,
    contest_note: str | None = None,
    tier_moved: bool | None = None,
) -> LcPrepublishRead:
    """Hold one delivered-but-unpublished reading. Returns the row.

    `lyrics` is required and is used for exactly one thing: scrubbing the
    agent's argument before it is stored. It is never written anywhere. See the
    module docstring for why the scrub cannot wait until publication.
    """
    calibration = dict(calibration or {})

    # Scrub the argument NOW, against the real lyrics, and keep only what the
    # guard hands back. Everything downstream trusts this line, so it is the
    # one place the prepublish lane can honestly claim "already checked".
    reasoning = calibration.get("reasoning")
    if reasoning:
        from app.services.calibration_corpus import _guard_reasoning
        calibration["reasoning"] = _guard_reasoning(
            reasoning, lyrics, title=title, artist=artist,
        )

    row = LcPrepublishRead(
        job_token=job_token,
        contest_of_id=contest_of_id,
        title=title,
        artist=artist,
        source=source,
        lyrics_fingerprint=lyrics_fingerprint,
        calibration_json=json.dumps(calibration, default=str),
        result_json=json.dumps(result_payload, default=str) if result_payload else None,
        user_id=user_id,
        device_id=device_id,
        ip_address=ip_address,
        status="held",
        contest_axis=contest_axis,
        contest_note=contest_note,
        tier_moved=tier_moved,
        environment=settings.environment,
    )
    db.add(row)
    db.commit()
    return row


def publish_read(db: Session, row: LcPrepublishRead, *, reason: str = "accepted") -> dict:
    """Commit one held reading to the Library. Idempotent by status.

    Runs the four deferred writes in the order the calibrate path always used:
    song row -> run ledger + consensus -> slug -> user attribution. Each stage
    after the first is fail-soft for the same reason it was fail-soft inline --
    the song is committed and the reading is durable, so a consensus or slug
    hiccup must not undo it.

    Returns {"song_id", "song_slug", "consensus"}.

    Idempotent by CLAIM, not by status read: see `_claim_for_publish` for why a
    Python-side status check is not enough when the reader and the sweep can
    reach the same row in the same second.
    """
    if not _claim_for_publish(db, row):
        logger.info("publish_read lost the claim on a %s row (id=%s); no-op",
                    row.status, row.id)
        return {"song_id": row.published_song_id, "song_slug": None, "consensus": None}

    from app.routers.songs import _get_or_create_slug
    from app.services.artist_linker import parse_artist_string
    from app.services.calibration_corpus import record_and_reconcile
    from app.services.song_sync import store_calibrated_song

    calibration = json.loads(row.calibration_json)

    song_id, _created = store_calibrated_song(
        db, source="submitted",
        title=row.title, artist=row.artist, calibration=calibration,
        ip_address=row.ip_address,
        ingestion_detail={"source": row.source, "prepublish": reason},
        artist_entries=parse_artist_string(row.artist or ""),
    )
    db.commit()

    consensus_info = None
    try:
        # lyrics_hash and lyrics_fingerprint: the fingerprint survived on the
        # hold row, the hash did not -- hashing needs the words, and the words
        # are gone by design. The fingerprint is what the divergence guard and
        # the contest check both actually use.
        result = record_and_reconcile(
            db,
            title=row.title, artist=row.artist,
            calibration=calibration,
            triggered_by="lyrical_charger",
            lyrics_fingerprint=row.lyrics_fingerprint,
            agent_model=calibration.get("agent_model"),
            direct_song_source="songs",
            direct_song_id=song_id,
            is_new_row=_created,
            # The argument was scrubbed at hold time, against the real lyrics.
            pre_scrubbed=True,
        )
        db.commit()
        consensus_info = result.get("consensus")
    except Exception:
        db.rollback()
        logger.exception("Corpus/consensus step failed on publish (non-fatal), read=%s", row.id)

    song_slug = None
    try:
        song_slug = _get_or_create_slug(row.title, row.artist, "songs", song_id, db)
    except Exception:
        logger.exception("song_slug resolution failed on publish (non-fatal), read=%s", row.id)

    if row.user_id is not None:
        try:
            from app.routers.analyzer import _record_user_calibration
            _record_user_calibration(
                db, user_id=row.user_id, song_id=song_id,
                song_slug=song_slug, title=row.title, artist=row.artist,
                calibration=calibration,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("user_calibration failed on publish (non-fatal), read=%s", row.id)

    row.status = "published"
    row.published_song_id = song_id
    row.published_at = _now()
    db.commit()

    return {"song_id": song_id, "song_slug": song_slug, "consensus": consensus_info}


def close_read(db: Session, row: LcPrepublishRead, status: str) -> None:
    """Close a held reading WITHOUT publishing it: contested (a re-read
    replaced it), declined (the reader rejected it and it escalated), or
    discarded (cleanup). Publication is not in this set and cannot be; see
    CLOSE_STATUSES."""
    if status not in CLOSE_STATUSES:
        raise ValueError(
            f"close_read cannot set {status!r}; "
            f"expected one of {CLOSE_STATUSES} (publishing goes through publish_read)"
        )
    if row.status == "held":
        row.status = status
        db.commit()


def _notify_abandoned(db: Session, row: LcPrepublishRead) -> None:
    """Email the admin when a reading that DREW AN OBJECTION publishes on the
    timer, so a person can confirm the publish rather than discover it.

    Silence counts as accepted here exactly as it does everywhere else -- a
    reader who walks away does not get their reading withheld, and nothing is
    refunded. But a reader who filed a contest and then left is not the same as
    a reader who never said anything, and the sweep is the only place that
    difference can still be acted on. Two shapes reach here:

      contest_of_id set  -- the re-read ran, the reader never took or refused
                            it, and the sweep published the more considered of
                            the two readings ('abandoned').
      contest_of_id NULL -- the re-read never ran at all (the model call failed,
                            and lc_contest stamped the objection onto the held
                            row before returning 503). What just published is
                            the reading the reader said was wrong, never
                            re-examined ('reread_failed').

    Fail-soft: an alert problem must never turn a published reading into a
    failed sweep row.
    """
    try:
        from app.services.alerts import emit_contest_filed
        from app.services.contest_guard import axis_label
        from app.constants import COLOR_LABELS

        own_tier = json.loads(row.calibration_json).get("rubric_color")
        if row.contest_of_id:
            first = (
                db.query(LcPrepublishRead)
                .filter(LcPrepublishRead.id == row.contest_of_id)
                .first()
            )
            first_tier = (
                json.loads(first.calibration_json).get("rubric_color") if first else None
            )
            second_tier, outcome = own_tier, "abandoned"
        else:
            first_tier, second_tier, outcome = own_tier, None, "reread_failed"

        emit_contest_filed(
            read_id=row.id,
            title=row.title, artist=row.artist,
            axis_label=axis_label(row.contest_axis),
            note=row.contest_note or "",
            first_tier=COLOR_LABELS.get(first_tier or "", first_tier or ""),
            second_tier=COLOR_LABELS.get(second_tier or "", second_tier or "") or None,
            tier_moved=bool(row.tier_moved),
            outcome=outcome,
        )
    except Exception:
        logger.exception("abandoned-contest notify failed (non-fatal) for read %s", row.id)


def sweep_expired(db: Session, *, limit: int = 200) -> dict:
    """Publish every held reading past HOLD_TTL. Returns a small report.

    This is the "silence means accepted" half, and it is also what closes the
    contested-then-abandoned case: a reader who contests and then leaves has a
    held RE-READ row, and the sweep publishes that one. It is the more
    considered of the two readings. Any row carrying an objection emails the
    admin on publish (see `_notify_abandoned`) -- silence still publishes, but
    never silently.

    Environment-filtered. Local dev shares the prod database through the tunnel,
    so an unfiltered sweep run from a laptop would publish prod readings.
    """
    # Take back anything a dead process left claimed. A crash between the claim
    # and the four writes would otherwise strand that reading in 'publishing'
    # forever, which is the one failure mode worse than a duplicate run: the
    # reader saw a reading that never reaches the Library and nothing ever looks
    # at the row again. Loud, because it should be rare enough to notice.
    stale = (
        db.query(LcPrepublishRead)
        .filter(LcPrepublishRead.status == "publishing")
        .filter(LcPrepublishRead.environment == settings.environment)
        .filter(LcPrepublishRead.updated_at < _now() - PUBLISHING_STALE)
        .update({"status": "held", "updated_at": _now()}, synchronize_session=False)
    )
    if stale:
        db.commit()
        logger.warning(
            "prepublish sweep reclaimed %s row(s) stranded in 'publishing'; "
            "a publish died mid-write", stale,
        )

    cutoff = _now() - HOLD_TTL
    rows = (
        db.query(LcPrepublishRead)
        .filter(LcPrepublishRead.status == "held")
        .filter(LcPrepublishRead.environment == settings.environment)
        .filter(LcPrepublishRead.created_at < cutoff)
        .order_by(LcPrepublishRead.created_at)
        .limit(limit)
        .all()
    )
    published, failed = 0, 0
    for row in rows:
        try:
            publish_read(db, row, reason="swept")
            published += 1
            # An objection that published on the timer gets a person told about
            # it. Checked AFTER the publish so the email only ever reports a
            # reading that really did land in the Library.
            if row.contest_axis:
                _notify_abandoned(db, row)
        except Exception:
            db.rollback()
            failed += 1
            logger.exception("Sweep publish failed for prepublish read %s", row.id)
    if rows:
        logger.info("prepublish sweep: %s published, %s failed", published, failed)
    return {"scanned": len(rows), "published": published, "failed": failed}
