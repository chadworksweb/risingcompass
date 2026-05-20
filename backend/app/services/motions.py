"""Motion Desk ops -- filing, validation, lifecycle transitions.

Motions deliberate the framework (tenets, rules, modifiers, methodology),
never a song's output. Songs may be cited as evidence inside reasoning
but are not targets.

motion_type:
  amend_tenet | new_tenet | remove_tenet
  amend_rule  | new_rule  | remove_rule
  process     -- methodology / morality / AI process. No target.

target_kind in (tenet, rule, modifier, NULL). The ref column carries
the id within that kind (e.g. 'violet-01', 'R1', 'contamination').

Filing requires Tier 2 (id_verified). Status lifecycle is one-way:
filed -> in_deliberation -> ratified | rejected | covered.
"""

import json
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Motion, User

logger = logging.getLogger(__name__)


# motion_type taxonomy
TENET_MOTION_TYPES = {"amend_tenet", "new_tenet", "remove_tenet"}
RULE_MOTION_TYPES = {"amend_rule", "new_rule", "remove_rule"}
PROCESS_MOTION_TYPES = {"process"}
VALID_MOTION_TYPES = TENET_MOTION_TYPES | RULE_MOTION_TYPES | PROCESS_MOTION_TYPES

# motion_types that operate on an existing artifact (require target_kind + target_ref)
TARGETED_TYPES = {"amend_tenet", "remove_tenet", "amend_rule", "remove_rule"}
# motion_types that propose a new artifact (no target_ref, target_kind optional and inferred)
NEW_TYPES = {"new_tenet", "new_rule"}
# motion_types for framework-level proposals (no target)
OPEN_TYPES = {"process"}

VALID_STATUSES = {"filed", "in_deliberation", "ratified", "rejected", "covered"}

VALID_TARGET_KINDS = {"tenet", "rule", "modifier"}

MAX_CLAIM_LENGTH = 280
MIN_REASONING_LENGTH = 50
MAX_REASONING_LENGTH = 5000
MAX_CITATIONS = 10
MAX_CITATION_URL_LENGTH = 500
MAX_TARGET_REF_LENGTH = 64


# ---------- gating + normalization ----------

def _require_tier2(user: User) -> None:
    if user.status != "active":
        raise HTTPException(status_code=403, detail="Account not active")
    if user.tier != "id_verified":
        raise HTTPException(
            status_code=403,
            detail="Motion filing requires identity verification. Verify in your account settings.",
        )
    if not user.handle:
        raise HTTPException(
            status_code=403,
            detail="Pick a handle before filing motions.",
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
                detail=f"Maximum {MAX_CITATIONS} citations per motion",
            )
    if not cleaned:
        return None
    return json.dumps(cleaned)


# ---------- target validation ----------

def _load_framework():
    """Lazy import + load to keep test/migration paths independent of the
    rubric_builder import chain at module load time."""
    from app.services.agents.rubric_builder import load_tenets
    return load_tenets()


def _known_tenet_ids() -> set[str]:
    data = _load_framework()
    ids: set[str] = set()
    for tier in data.get("tiers", []):
        for tenet in tier.get("tenets", []):
            tid = tenet.get("id")
            if tid:
                ids.add(tid)
    return ids


def _known_rule_ids() -> set[str]:
    data = _load_framework()
    return {r.get("id") for r in data.get("rules", []) if r.get("id")}


def _known_modifier_ids() -> set[str]:
    data = _load_framework()
    return {m.get("id") for m in data.get("modifiers", []) if m.get("id")}


def _validate_target(motion_type: str, target_kind: Optional[str], target_ref: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return the cleaned (kind, ref) tuple. Raises 422 on mismatch."""
    if motion_type in OPEN_TYPES:
        if target_kind or target_ref:
            raise HTTPException(
                status_code=422,
                detail=f"{motion_type} motions do not take a target",
            )
        return None, None

    if motion_type in NEW_TYPES:
        if target_ref:
            raise HTTPException(
                status_code=422,
                detail=f"{motion_type} motions propose a new artifact -- no target_ref",
            )
        # For new_tenet/new_rule, derive target_kind from the motion_type so
        # the row carries the implied taxonomy slot.
        implied_kind = "tenet" if motion_type == "new_tenet" else "rule"
        if target_kind and target_kind != implied_kind:
            raise HTTPException(
                status_code=422,
                detail=f"{motion_type} implies target_kind='{implied_kind}'",
            )
        return implied_kind, None

    # Targeted types: amend/remove of tenet/rule. modifier amends are
    # allowed via amend_rule (no separate amend_modifier type), so accept
    # target_kind='modifier' when motion_type='amend_rule' (the modifier
    # being a rule-like procedural artifact). For amend_tenet/remove_tenet,
    # kind is locked to 'tenet'.
    if motion_type in {"amend_tenet", "remove_tenet"}:
        kind = "tenet"
        if target_kind and target_kind != "tenet":
            raise HTTPException(
                status_code=422,
                detail=f"{motion_type} requires target_kind='tenet'",
            )
    elif motion_type in {"amend_rule", "remove_rule"}:
        kind = (target_kind or "rule").strip().lower()
        if kind not in {"rule", "modifier"}:
            raise HTTPException(
                status_code=422,
                detail=f"{motion_type} requires target_kind in (rule, modifier)",
            )
    else:
        raise HTTPException(status_code=422, detail=f"Unknown motion_type: {motion_type}")

    if not target_ref:
        raise HTTPException(
            status_code=422,
            detail=f"{motion_type} requires target_ref (the id within target_kind)",
        )
    ref = target_ref.strip()
    if len(ref) > MAX_TARGET_REF_LENGTH:
        raise HTTPException(status_code=422, detail="target_ref too long")

    if kind == "tenet":
        if ref.lower() not in _known_tenet_ids():
            raise HTTPException(
                status_code=422,
                detail=f"target_ref '{ref}' does not match any tenet in core.json",
            )
        ref = ref.lower()
    elif kind == "rule":
        known = _known_rule_ids()
        if ref not in known:
            raise HTTPException(
                status_code=422,
                detail=f"target_ref '{ref}' does not match any rule in core.json",
            )
    elif kind == "modifier":
        if ref.lower() not in _known_modifier_ids():
            raise HTTPException(
                status_code=422,
                detail=f"target_ref '{ref}' does not match any modifier in core.json",
            )
        ref = ref.lower()
    return kind, ref


# ---------- public ops ----------

def file_motion(
    db: Session,
    user: User,
    motion_type: str,
    target_kind: Optional[str],
    target_ref: Optional[str],
    claim: str,
    reasoning: str,
    citations: Optional[list],
) -> Motion:
    _require_tier2(user)
    if motion_type not in VALID_MOTION_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"motion_type must be one of {sorted(VALID_MOTION_TYPES)}",
        )
    kind, ref = _validate_target(motion_type, target_kind, target_ref)
    claim_clean = _normalize_text(claim, "claim", min_len=1, max_len=MAX_CLAIM_LENGTH)
    reasoning_clean = _normalize_text(
        reasoning, "reasoning",
        min_len=MIN_REASONING_LENGTH, max_len=MAX_REASONING_LENGTH,
    )
    citations_json = _validate_citations(citations)

    motion = Motion(
        motion_type=motion_type,
        target_kind=kind,
        target_ref=ref,
        claim=claim_clean,
        reasoning=reasoning_clean,
        citations=citations_json,
        filed_by_user_id=user.id,
        status="filed",
    )
    db.add(motion)
    db.commit()
    db.refresh(motion)
    logger.info(
        "Motion filed: %s id=%s by user=%s kind=%s ref=%s",
        motion_type, motion.id, user.id, kind, ref,
    )
    return motion


def transition_status(
    db: Session,
    motion: Motion,
    new_status: str,
    admin_user_id: int,
    resolution_summary: Optional[str] = None,
) -> Motion:
    """Lifecycle:
      filed -> in_deliberation | rejected
      in_deliberation -> ratified | rejected | covered

    resolution_summary required (10+ chars) for any terminal transition.
    """
    if new_status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"Unknown status: {new_status}")
    if new_status == "filed":
        raise HTTPException(status_code=422, detail="Cannot revert to filed")

    if motion.status == "filed":
        if new_status not in {"in_deliberation", "rejected"}:
            raise HTTPException(
                status_code=409,
                detail="From filed, only in_deliberation or rejected are allowed",
            )
    elif motion.status == "in_deliberation":
        if new_status not in {"ratified", "rejected", "covered"}:
            raise HTTPException(
                status_code=409,
                detail="From in_deliberation, only ratified/rejected/covered are allowed",
            )
    else:
        raise HTTPException(
            status_code=409,
            detail=f"Motion already resolved (status={motion.status})",
        )

    is_resolution = new_status in {"ratified", "rejected", "covered"}
    if is_resolution:
        if not resolution_summary or len(resolution_summary.strip()) < 10:
            raise HTTPException(
                status_code=422,
                detail="resolution_summary required (10+ chars) when closing a motion",
            )
        motion.resolution_summary = resolution_summary.strip()
        motion.resolved_at = datetime.utcnow()
        motion.resolved_by_admin_id = admin_user_id

    motion.status = new_status
    db.commit()
    db.refresh(motion)
    logger.info(
        "Motion %s transitioned to %s by admin=%s", motion.id, new_status, admin_user_id,
    )
    return motion


def motion_to_dict(
    db: Session,
    motion: Motion,
    *,
    include_filer_handle: bool = True,
) -> dict:
    citations: list = []
    if motion.citations:
        try:
            parsed = json.loads(motion.citations)
            if isinstance(parsed, list):
                citations = [c for c in parsed if isinstance(c, str)]
        except (ValueError, TypeError):
            citations = []

    out: dict = {
        "id": motion.id,
        "motion_type": motion.motion_type,
        "status": motion.status,
        "target_kind": motion.target_kind,
        "target_ref": motion.target_ref,
        "claim": motion.claim,
        "reasoning": motion.reasoning,
        "citations": citations,
        "filed_at": motion.filed_at.isoformat() + "Z" if motion.filed_at else None,
        "resolved_at": motion.resolved_at.isoformat() + "Z" if motion.resolved_at else None,
        "resolution_summary": motion.resolution_summary,
    }

    if include_filer_handle and motion.filed_by_user_id:
        filer = db.query(User).get(motion.filed_by_user_id)
        if filer is not None:
            out["filed_by_handle"] = filer.handle
            out["filed_by_anon_id"] = filer.anon_id
            out["filed_by_verified"] = filer.tier == "id_verified"

    return out
