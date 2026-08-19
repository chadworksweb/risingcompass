"""The Lyrical Charger contest lane: the rung between "correct" and "file a report".

Three endpoints, one per thing a reader can do with a held reading:

    POST /api/analyzer/contest   the reading missed something -> one re-read
    POST /api/analyzer/accept    this reading is right -> publish it
    POST /api/analyzer/decline   the re-read is still wrong -> escalate

Say nothing at all and the sweep publishes on the TTL, because silence has to
mean accepted or the Library starves on its main intake (see lc_publish).

THE READER PASTES THE LYRICS AGAIN, and there is no way around it: charger.js
clears the input the moment a reading lands, and nothing anywhere stores the
words. What the hold row carries is the one-way fingerprint, which is enough to
check the re-paste is the SAME SONG (max_jaccard against DIVERGENCE_THRESHOLD,
the same pair the Layer 2 divergence guard uses) without ever having retained
what it is checking.

ONE re-read, ever. The second model call is the entire token cost of this
feature, and rung three costs nothing: a declined re-read lands in
misread_submissions, where a human already has a queue, a ban list, and a
recalibration pipeline waiting for it.
"""

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import optional_clerk_user, verify_api_or_service_key
from app.constants import COLOR_LABELS
from app.database import get_db
from app.models import LcPrepublishRead, MisreadSubmission, User
from app.services import lc_publish
from app.services.contest_guard import CONTEST_AXES, axis_label, check_contest
from app.services.lyrics_fingerprint import (
    compute_fingerprint, max_jaccard, DIVERGENCE_THRESHOLD,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analyzer", tags=["lc-contest"])


class ContestIn(BaseModel):
    job_token: str
    axis: str = Field(..., description="one of contest_guard.CONTEST_AXES")
    note: str = Field(..., description="the reader's pointer at a line")
    lyrics: str = Field(..., description="re-pasted by the reader; never stored")


class TokenIn(BaseModel):
    job_token: str


class DeclineIn(BaseModel):
    job_token: str
    message: str | None = Field(default=None, description="optional, for the queue")
    email: str | None = Field(
        default=None,
        description="optional reply address; offered to signed-out readers, who "
                    "are otherwise unreachable. A signed-in reader's account "
                    "address is fetched instead. See _reader_email.",
    )


def _latest_held(db: Session, job_token: str) -> LcPrepublishRead:
    """The row a reader's action applies to: the newest HELD reading for their
    token. On a contested job that is the re-read, which is what makes accept
    and decline mean the right thing without the client tracking read ids."""
    row = (
        db.query(LcPrepublishRead)
        .filter(LcPrepublishRead.job_token == job_token)
        .filter(LcPrepublishRead.status == "held")
        .order_by(LcPrepublishRead.id.desc())
        .first()
    )
    if row is None:
        # Deliberately one message for "never existed" and "already closed".
        # A held reading is only ever addressable by the person holding the
        # token, and the sweep may have published it seconds ago, which is not
        # an error the reader can act on.
        raise HTTPException(404, "That reading is no longer awaiting your response.")
    return row


def _tier_of(calibration: dict) -> str | None:
    return calibration.get("rubric_color")


_EMAIL_MAX = 254


def _looks_like_email(value: str) -> bool:
    """Deliberately shallow. This address is a convenience for replying, not a
    credential, and a reader whose perfectly good address trips a clever regex
    would have their whole report rejected over it."""
    at = value.count("@")
    return at == 1 and " " not in value and "." in value.rsplit("@", 1)[-1]


def _reader_email(db: Session, row: LcPrepublishRead, supplied: str | None) -> str | None:
    """The address a person can answer this report at, or None.

    Two sources, in this order:

      1. Whatever the reader typed. The decline step offers an optional field to
         signed-OUT readers, who are otherwise unreachable -- and they are the
         majority of the Charger's traffic.
      2. The signed-in reader's account address, fetched from Clerk. RC stores
         no plaintext email (only `users.email_hash`), so there is nowhere local
         to read it from; it has to be asked for at decline time.

    Fail-soft in both directions. A Clerk outage, a malformed address, or a
    reader who left the field blank files the report anyway -- unanswerable
    beats unfiled, and this rung exists precisely because the reader has already
    told us twice that the reading is wrong.
    """
    typed = (supplied or "").strip()[:_EMAIL_MAX]
    if typed and _looks_like_email(typed):
        return typed

    if row.user_id is None:
        return None
    try:
        from app.services.clerk import get_clerk_user_email
        user = db.query(User).filter(User.id == row.user_id).first()
        if user is None or not user.clerk_user_id:
            return None
        return get_clerk_user_email(user.clerk_user_id)
    except Exception:
        logger.exception("reader email lookup failed (non-fatal) for read %s", row.id)
        return None


def _notify(db: Session, row: LcPrepublishRead, outcome: str) -> None:
    """Email the admin the whole exchange, once, after the reader decides.

    Fail-soft in every direction: an alert problem must never turn a decided
    contest into an error for the reader.

    Reads the first reading through the CALLER'S session. It used to open its
    own `SessionLocal()` and never close it, leaking a pooled connection on
    every contest decision -- and for nothing, since the row it wants sits in
    the same database the request is already holding open.
    """
    try:
        from app.services.alerts import emit_contest_filed
        first = (
            db.query(LcPrepublishRead)
            .filter(LcPrepublishRead.id == row.contest_of_id)
            .first()
            if row.contest_of_id else None
        )
        first_tier = _tier_of(json.loads(first.calibration_json)) if first else None
        second_tier = _tier_of(json.loads(row.calibration_json))
        emit_contest_filed(
            read_id=row.id,
            title=row.title, artist=row.artist,
            axis_label=axis_label(row.contest_axis),
            note=row.contest_note or "",
            first_tier=COLOR_LABELS.get(first_tier or "", first_tier or ""),
            second_tier=COLOR_LABELS.get(second_tier or "", second_tier or ""),
            tier_moved=bool(row.tier_moved),
            outcome=outcome,
        )
    except Exception:
        logger.exception("contest notify failed (non-fatal) for read %s", row.id)


@router.post("/contest")
async def contest_reading(
    body: ContestIn,
    request: Request,
    background_tasks: BackgroundTasks,
    tier: str = Depends(verify_api_or_service_key),
    current_user: User | None = Depends(optional_clerk_user),
    db: Session = Depends(get_db),
):
    """Contest one held reading. Runs exactly one re-read and returns it."""
    row = _latest_held(db, body.job_token)

    if row.contest_of_id is not None:
        # Already a re-read. The next step is a person, not another call.
        raise HTTPException(
            409,
            "This reading has already been re-read once. If it is still wrong, "
            "send it to review.",
        )

    # SAME SONG FIRST, then the note. The fingerprint is all we kept, and all we
    # need -- and the order matters more than it looks. The guard checks the
    # reader's pointer against the lyrics they just pasted, so a reader who
    # pastes the WRONG song (two tabs open, easily done) has a note quoting the
    # right song and lyrics that do not contain it. Checked the other way round
    # they were told "quote the line you mean", which sends them off rewriting a
    # note that was correct all along while the actual mistake sat untouched in
    # the box below it. Establish which song this is, then judge the pointer.
    new_fp = compute_fingerprint(body.lyrics)
    if row.lyrics_fingerprint and new_fp:
        if max_jaccard(new_fp, [row.lyrics_fingerprint]) < DIVERGENCE_THRESHOLD:
            raise HTTPException(422, {
                "error": "lyrics_diverge",
                "message": ("Those look like different lyrics from the ones that "
                            "were read. Paste the same song's lyrics again."),
            })

    error = check_contest(body.axis, body.note, body.lyrics)
    if error:
        raise HTTPException(422, {"error": "contest_rejected", "message": error})

    axis = CONTEST_AXES[body.axis]
    calibration = json.loads(row.calibration_json)

    from app.services.agents.calibrator import calibrate_song_async
    re_read = await calibrate_song_async(
        row.title, row.artist, body.lyrics,
        db=None, skip_cache=True,
        contest_directs=axis["directs"],
        contest_note=body.note.strip(),
    )
    if not re_read or not re_read.get("rubric_color"):
        # RECORD THE OBJECTION EVEN THOUGH THE RE-READ DIED. The reading stays
        # held and the sweep will publish it on the TTL, because silence counts
        # as accepted here as everywhere -- but a reader who said "this is
        # wrong" and got a 503 is not the same as a reader who said nothing, and
        # the difference has to survive somewhere. Stamping the axis and note on
        # the held row is what lets the sweep email the admin to confirm the
        # publish instead of committing an objected-to reading in silence.
        #
        # `contest_of_id` stays NULL, so this is not the reader's one re-read
        # spent: the 409 guard above still lets them try again.
        row.contest_axis = body.axis
        row.contest_note = body.note.strip()
        db.commit()
        raise HTTPException(503, {
            "error": "reread_failed",
            "message": ("The re-read could not be completed. Your first reading "
                        "is still held -- try again, or send it to review."),
        })

    first_tier = _tier_of(calibration)
    second_tier = _tier_of(re_read)

    result_payload = {
        "status": "scored",
        "tier": second_tier,
        "tier_label": COLOR_LABELS.get(second_tier),
        "charge": re_read.get("charge_value"),
        "contaminated": re_read.get("contaminated", False),
        "contamination_note": re_read.get("contamination_note"),
        "charge_summary": re_read.get("charge_summary"),
        "confidence": re_read.get("confidence", 0.0),
        "title": row.title,
        "artist": row.artist,
        "listener_effects_prose": re_read.get("listener_effects_prose"),
        "societal_effects_prose": re_read.get("societal_effects_prose"),
        "deadpan_line": re_read.get("deadpan_line"),
        "topics": re_read.get("topics"),
    }

    second = lc_publish.hold_read(
        db,
        job_token=row.job_token,
        title=row.title, artist=row.artist, source=row.source,
        calibration=re_read,
        result_payload=result_payload,
        lyrics=body.lyrics,
        lyrics_fingerprint=new_fp or row.lyrics_fingerprint,
        user_id=row.user_id,
        device_id=row.device_id,
        ip_address=row.ip_address,
        contest_of_id=row.id,
        contest_axis=body.axis,
        contest_note=body.note.strip(),
        tier_moved=(first_tier != second_tier),
    )

    # The original is done. It never published and it never will.
    lc_publish.close_read(db, row, "contested")

    return {
        "status": "re_read",
        "read_id": second.id,
        "tier_moved": second.tier_moved,
        "result": result_payload,
    }


@router.post("/accept")
async def accept_reading(
    body: TokenIn,
    request: Request,
    tier: str = Depends(verify_api_or_service_key),
    db: Session = Depends(get_db),
):
    """Publish the held reading. This is the only place a Lyrical Charger
    reading enters the Library on purpose rather than on a timer."""
    row = _latest_held(db, body.job_token)
    published = lc_publish.publish_read(db, row, reason="accepted")
    if row.contest_of_id is not None:
        _notify(db, row, "accepted")
    return {
        "status": "published",
        "song_slug": published.get("song_slug"),
        "consensus": published.get("consensus"),
    }


@router.post("/decline")
async def decline_reading(
    body: DeclineIn,
    request: Request,
    tier: str = Depends(verify_api_or_service_key),
    db: Session = Depends(get_db),
):
    """The re-read is still wrong. Escalate to the misread queue and publish
    nothing.

    Rung three, and it costs no tokens at all: a person picks it up from a queue
    that already has a ban list and a recalibration pipeline behind it. The
    reading itself is closed unpublished -- a reader who says twice that it is
    wrong should not have it land in the Library on a timer.
    """
    row = _latest_held(db, body.job_token)
    calibration = json.loads(row.calibration_json)
    color = _tier_of(calibration)

    message = (body.message or "").strip()
    if not message:
        # Fall back to what they already told us rather than making them type
        # the same objection a second time.
        message = row.contest_note or "Reader declined the re-read."

    submission = MisreadSubmission(
        song_title=row.title,
        song_artist=row.artist,
        song_color=color or "green",
        user_id=row.user_id,
        email=_reader_email(db, row, body.email),
        message=message,
        device_id=row.device_id,
        ip_address=row.ip_address,
        status="pending",
        report_type="misread",
    )
    db.add(submission)
    lc_publish.close_read(db, row, "declined")
    db.commit()

    if row.contest_of_id is not None:
        _notify(db, row, "declined")

    return {"status": "escalated", "submission_id": submission.id}


@router.get("/contest/axes")
async def contest_axes(tier: str = Depends(verify_api_or_service_key)):
    """The closed axis vocabulary, so the result screen renders the same list
    the guard validates against instead of a second copy that can drift."""
    return {
        "axes": [
            {"key": key, "label": entry["label"]}
            for key, entry in CONTEST_AXES.items()
        ]
    }
