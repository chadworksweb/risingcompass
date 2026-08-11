"""Admin Ether Audits — triage queue for songs the taxonomy couldn't fit.

A song lands in the queue two ways: the ether tagger writes a `topic_audit`
when it can't honestly fit a song into the closed 32-topic taxonomy, OR an
admin opens an audit on an already-tagged song whose tags are wrong. Either
way the row carries `topics: []` + a `topic_audit` payload.

This router exposes the queue plus one entry action and three triage actions:

  - **Audit** — open an audit on a published, already-tagged song whose ether
    tags are wrong: clears `topics` to [] and writes a `topic_audit`. The
    entry point the tagger's own escape-hatch can't provide.
  - **Remap** — pick an existing slug; the agent missed it.
  - **Retag** — re-run the tagger after the admin has added a new slug
    to `services/ether_taxonomy.py` and deployed.
  - **Reject** — clear the audit; the song genuinely doesn't have a
    topic worth tagging.

"Accept new tag" in the scope doc is **not** a single endpoint. It's
two steps: the admin commits a code change to `ether_taxonomy.py`
(taxonomy expansion is rare and deserves review), deploys, then calls
**Retag** so the tagger uses the new VALID_SLUGS. This split is
deliberate per scope §250.

All endpoints are session-gated (`require_admin_session` via the legacy
`verify_admin_key` alias) so unauthed callers see 401, not data.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Song
from app.routers.admin import verify_admin_key
from app.services.ether_taxonomy import valid_slugs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/ether-audits", tags=["ether-audits"])


# --- Schemas ---

class AuditItem(BaseModel):
    id: int
    title: str
    artist: str
    year: Optional[int]
    chart_position: Optional[int]
    rubric_color: str
    charge_value: Optional[int]
    deadpan_line: Optional[str]
    reason: str
    proposed_tag: str
    rationale: str
    created_at: Optional[str]


class AuditQueueOut(BaseModel):
    items: list[AuditItem]
    total: int


class RemapIn(BaseModel):
    topic: str = Field(..., description="Existing taxonomy slug to remap to")
    note: Optional[str] = None


class RejectIn(BaseModel):
    note: Optional[str] = None


class RetagIn(BaseModel):
    lyrics: str = Field(..., min_length=1)


class AuditIn(BaseModel):
    reason: str = Field(..., min_length=1, description="Why the current tags are wrong")
    rationale: str = Field(..., min_length=1, description="What the honest read is / why no slug fits")
    proposed_tag: Optional[str] = Field(None, description="Suggested new taxonomy slug, if one should exist")


class ActionOut(BaseModel):
    song_id: int
    deadpan_line: Optional[str] = None
    topics: list[str] = []
    topic_audit: Optional[dict] = None
    status: str  # "audited" | "remapped" | "rejected" | "retagged" | "retag_audit_persisted"


# --- Helpers ---

def _decode_audit(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _serialize_topics(topics: list[str]) -> str:
    return json.dumps(topics)


def _decode_topics(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _latest_appearance(db: Session, song_id: int) -> tuple[Optional[int], Optional[int]]:
    """Year + chart position from the song's most-recent chart appearance.

    Charting moved out of the song row into `chart_appearances`; the unified
    `Song` has no year/position. A manual/library song with no appearance
    returns (None, None) -- the queue item then carries nulls.
    """
    row = db.execute(
        text(
            "SELECT year, position FROM chart_appearances "
            "WHERE song_id = :sid "
            "ORDER BY year DESC NULLS LAST, id DESC LIMIT 1"
        ),
        {"sid": song_id},
    ).first()
    if not row:
        return None, None
    return row[0], row[1]


def _load_audit_song(db: Session, song_id: int) -> Song:
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(404, "song not found")
    if not song.topic_audit:
        raise HTTPException(409, "song has no open audit")
    return song


# --- Endpoints ---

@router.get("/queue", response_model=AuditQueueOut, dependencies=[Depends(verify_admin_key)])
def list_queue(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Return the open audit queue, newest first."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    base = db.query(Song).filter(Song.topic_audit.isnot(None))
    total = base.count()
    rows = (
        base.order_by(Song.created_at.desc().nullslast(), Song.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = []
    for row in rows:
        audit = _decode_audit(row.topic_audit)
        year, position = _latest_appearance(db, row.id)
        items.append(AuditItem(
            id=row.id,
            title=row.title,
            artist=row.artist,
            year=year,
            chart_position=position,
            rubric_color=row.rubric_color,
            charge_value=row.charge_value,
            deadpan_line=row.deadpan_line,
            reason=str(audit.get("reason") or ""),
            proposed_tag=str(audit.get("proposed_tag") or ""),
            rationale=str(audit.get("rationale") or ""),
            created_at=row.created_at.isoformat() if row.created_at else None,
        ))
    return AuditQueueOut(items=items, total=total)


@router.post("/{song_id}/audit", response_model=ActionOut, dependencies=[Depends(verify_admin_key)])
def audit_song(
    song_id: int,
    data: AuditIn,
    db: Session = Depends(get_db),
):
    """Open an audit on an already-tagged song whose ether tags are wrong.

    The entry point the tagger's own audit escape-hatch can't provide: a human
    (or Claude Code) decides a published song's ether tags are wrong, so we
    clear `topics` to [] and write a `topic_audit` payload. The song then
    surfaces in the panel where remap / retag / reject resolve it. The prior
    topics are preserved in the audit under `audited_from` for the record.

    Anthropic-free: the reason/rationale/proposed_tag are supplied, not generated.
    """
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        raise HTTPException(404, "song not found")

    prior_topics = _decode_topics(song.topics)
    audit = {
        "reason": data.reason.strip(),
        "proposed_tag": (data.proposed_tag or "").strip(),
        "rationale": data.rationale.strip(),
        "audited_from": prior_topics,
        "source": "admin_audit",
    }
    song.topics = _serialize_topics([])
    song.topic_audit = json.dumps(audit)
    db.commit()
    db.refresh(song)
    logger.info("Ether tags audited: song=%s audited_from=%s proposed=%r",
                song.id, prior_topics, audit["proposed_tag"])
    return ActionOut(
        song_id=song.id,
        deadpan_line=song.deadpan_line,
        topics=[],
        topic_audit=audit,
        status="audited",
    )


@router.post("/{song_id}/remap", response_model=ActionOut, dependencies=[Depends(verify_admin_key)])
def remap_audit(
    song_id: int,
    data: RemapIn,
    db: Session = Depends(get_db),
):
    """Remap an audited song to an existing taxonomy slug.

    Writes the slug into `topics` (single-element list) and clears
    `topic_audit`. The deadpan_line is preserved.
    """
    topic = (data.topic or "").strip()
    if topic not in valid_slugs(db):
        raise HTTPException(400, f"'{topic}' is not in the current taxonomy")

    song = _load_audit_song(db, song_id)
    song.topics = _serialize_topics([topic])
    song.topic_audit = None
    db.commit()
    db.refresh(song)
    logger.info("Ether audit remapped: song=%s topic=%s note=%r", song.id, topic, data.note)
    return ActionOut(
        song_id=song.id,
        deadpan_line=song.deadpan_line,
        topics=[topic],
        topic_audit=None,
        status="remapped",
    )


@router.post("/{song_id}/reject", response_model=ActionOut, dependencies=[Depends(verify_admin_key)])
def reject_audit(
    song_id: int,
    data: RejectIn,
    db: Session = Depends(get_db),
):
    """Close the audit without writing topics. The song stays with
    `topics: []` — agent and admin agree no taxonomy slug fits."""
    song = _load_audit_song(db, song_id)
    song.topics = _serialize_topics([])
    song.topic_audit = None
    db.commit()
    db.refresh(song)
    logger.info("Ether audit rejected: song=%s note=%r", song.id, data.note)
    return ActionOut(
        song_id=song.id,
        deadpan_line=song.deadpan_line,
        topics=[],
        topic_audit=None,
        status="rejected",
    )


@router.post("/{song_id}/retag", response_model=ActionOut, dependencies=[Depends(verify_admin_key)])
def retag_audit(
    song_id: int,
    data: RetagIn,
    db: Session = Depends(get_db),
):
    """Re-run the ether tagger against the song with the **current**
    taxonomy. Used after the admin has added a new slug to
    `services/ether_taxonomy.py` and deployed (Accept-new-tag flow).

    Loop guard (build pack §9): if the agent audits again, persist the
    new audit payload and surface it for human eyes. The caller can
    decide whether to retry, remap, or reject.
    """
    from app.services.agents.ether_tagger import tag_song

    song = _load_audit_song(db, song_id)

    ether = tag_song(
        title=song.title,
        artist=song.artist,
        lyrics=data.lyrics,
        rubric_color=song.rubric_color,
        charge_value=song.charge_value,
        charge_summary=song.charge_summary,
        listener_effects_prose=song.listener_effects_prose,
    )
    if not ether:
        raise HTTPException(502, "Ether tagger returned no result; check server logs")

    song.deadpan_line = ether["deadpan_line"]
    song.topics = _serialize_topics(ether["topics"])
    song.topic_audit = (
        json.dumps(ether["topic_audit"]) if ether["topic_audit"] else None
    )
    db.commit()
    db.refresh(song)

    status = "retag_audit_persisted" if ether["topic_audit"] else "retagged"
    logger.info(
        "Ether audit retagged: song=%s topics=%s status=%s",
        song.id, ether["topics"], status,
    )
    return ActionOut(
        song_id=song.id,
        deadpan_line=song.deadpan_line,
        topics=ether["topics"],
        topic_audit=ether["topic_audit"],
        status=status,
    )
