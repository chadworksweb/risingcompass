"""Public Participation Phase 4 -- Deliberation Chamber ops.

The Chamber hosts the structured argument thread for a motion in
in_deliberation. Five typed post kinds (argument_for, argument_against,
rebuttal, citation, clarification). Rebuttals reference a parent post on
the same motion; every other type is top-level (parent_id NULL).

Tier 2 (id_verified) gates writes. Reads are public. Posting on a
motion that is not in_deliberation is a 409 (filed motions aren't open
yet; resolved motions are sealed). The thread itself stays public after
resolution -- it just becomes read-only.
"""

import json
import logging
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Motion, MotionArgument, User

logger = logging.getLogger(__name__)


VALID_POST_TYPES = {
    "argument_for",
    "argument_against",
    "rebuttal",
    "citation",
    "clarification",
}

# Only rebuttals attach to a parent. Everything else is top-level.
REBUTTAL_TYPES = {"rebuttal"}

MAX_SUMMARY_LENGTH = 280
MIN_BODY_LENGTH = 50
MAX_BODY_LENGTH = 5000
MAX_CITATIONS = 10
MAX_CITATION_URL_LENGTH = 500


# ---------- gating + normalization ----------

def _require_tier2(user: User) -> None:
    if user.status != "active":
        raise HTTPException(status_code=403, detail="Account not active")
    if user.tier != "id_verified":
        raise HTTPException(
            status_code=403,
            detail="Posting in the Deliberation Chamber requires identity verification.",
        )
    if not user.handle:
        raise HTTPException(
            status_code=403,
            detail="Pick a handle before posting.",
        )


def _normalize_text(raw: Optional[str], field: str, *, min_len: int, max_len: int) -> str:
    if raw is None:
        raise HTTPException(status_code=422, detail=f"{field} required")
    cleaned = raw.strip()
    if len(cleaned) < min_len:
        raise HTTPException(
            status_code=422,
            detail=f"{field} must be at least {min_len} characters",
        )
    if len(cleaned) > max_len:
        raise HTTPException(
            status_code=422,
            detail=f"{field} exceeds {max_len} characters",
        )
    return cleaned


def _validate_citations(raw: Optional[list]) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise HTTPException(status_code=422, detail="citations must be a list of URLs")
    cleaned: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise HTTPException(status_code=422, detail="citations must be URL strings")
        url = item.strip()
        if not url:
            continue
        if len(url) > MAX_CITATION_URL_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=f"citation URL exceeds {MAX_CITATION_URL_LENGTH} characters",
            )
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(status_code=422, detail=f"Invalid citation URL: {url}")
        cleaned.append(url)
        if len(cleaned) > MAX_CITATIONS:
            raise HTTPException(
                status_code=422,
                detail=f"Maximum {MAX_CITATIONS} citations per post",
            )
    if not cleaned:
        return None
    return json.dumps(cleaned)


# ---------- post ----------

def post_argument(
    db: Session,
    user: User,
    motion: Motion,
    post_type: str,
    parent_id: Optional[int],
    summary: str,
    body: str,
    citations: Optional[list],
) -> MotionArgument:
    _require_tier2(user)
    if post_type not in VALID_POST_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"post_type must be one of {sorted(VALID_POST_TYPES)}",
        )
    if motion.status != "in_deliberation":
        raise HTTPException(
            status_code=409,
            detail=(
                "Chamber is closed: motion is not in deliberation "
                f"(status={motion.status})"
            ),
        )

    # Rebuttals need a parent on the same motion that is itself a
    # top-level post. Non-rebuttals are top-level and must not carry a
    # parent_id. The schema is permissive; the rule is here.
    if post_type in REBUTTAL_TYPES:
        if parent_id is None:
            raise HTTPException(
                status_code=422,
                detail="rebuttal requires parent_id (the post it answers)",
            )
        parent = db.query(MotionArgument).get(parent_id)
        if parent is None:
            raise HTTPException(status_code=422, detail="parent_id does not exist")
        if parent.motion_id != motion.id:
            raise HTTPException(
                status_code=422,
                detail="parent_id belongs to a different motion",
            )
        if parent.parent_id is not None:
            raise HTTPException(
                status_code=422,
                detail="Rebuttals can only attach to top-level posts (2-level depth cap)",
            )
        # Don't let users rebut their own arguments -- nothing to debate.
        if parent.user_id == user.id:
            raise HTTPException(
                status_code=422,
                detail="You cannot rebut your own post",
            )
    else:
        if parent_id is not None:
            raise HTTPException(
                status_code=422,
                detail=f"{post_type} posts cannot carry parent_id",
            )

    summary_clean = _normalize_text(
        summary, "summary", min_len=1, max_len=MAX_SUMMARY_LENGTH,
    )
    body_clean = _normalize_text(
        body, "body", min_len=MIN_BODY_LENGTH, max_len=MAX_BODY_LENGTH,
    )
    citations_json = _validate_citations(citations)

    arg = MotionArgument(
        motion_id=motion.id,
        user_id=user.id,
        post_type=post_type,
        parent_id=parent_id,
        summary=summary_clean,
        body=body_clean,
        citations=citations_json,
    )
    db.add(arg)
    db.commit()
    db.refresh(arg)
    logger.info(
        "Chamber post: motion=%s id=%s type=%s by=%s parent=%s",
        motion.id, arg.id, post_type, user.id, parent_id,
    )
    return arg


# ---------- serialization ----------

def argument_to_dict(db: Session, arg: MotionArgument) -> dict:
    citations: list = []
    if arg.citations:
        try:
            parsed = json.loads(arg.citations)
            if isinstance(parsed, list):
                citations = [c for c in parsed if isinstance(c, str)]
        except (ValueError, TypeError):
            citations = []

    out: dict = {
        "id": arg.id,
        "motion_id": arg.motion_id,
        "post_type": arg.post_type,
        "parent_id": arg.parent_id,
        "summary": arg.summary,
        "body": arg.body,
        "citations": citations,
        "created_at": arg.created_at.isoformat() + "Z" if arg.created_at else None,
    }
    author = db.query(User).get(arg.user_id) if arg.user_id else None
    if author is not None:
        out["author_handle"] = author.handle
        out["author_anon_id"] = author.anon_id
        out["author_verified"] = author.tier == "id_verified"
    return out
