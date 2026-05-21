"""Amendment log loader.

Merges the static config (venue, thresholds, statuses, examples,
schema_version, updated_at) from log.json with live motions from the
motions table.

What counts as an "amendment" for the public log: every motion that
deliberates a piece of the framework. That is currently every motion
type -- tenets, rules, modifiers, and process. If a future motion
class targets something outside the framework (we don't have one
today) the type filter here is the gate.

Status mapping (motions -> public amendment log):
  filed             -> proposed
  in_deliberation   -> deliberating
  ratified          -> ratified
  rejected          -> rejected
  covered           -> covered
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Motion, User
from app.services.motions import VALID_MOTION_TYPES

_LOG_PATH = Path(__file__).parent / "log.json"

_STATUS_MAP = {
    "filed": "proposed",
    "in_deliberation": "deliberating",
    "ratified": "ratified",
    "rejected": "rejected",
    "covered": "covered",
}


@lru_cache(maxsize=1)
def _load_log_config() -> dict:
    """Static framing from log.json. Cached once at first read."""
    with _LOG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _format_date(dt) -> Optional[str]:
    if dt is None:
        return None
    try:
        return dt.date().isoformat()
    except AttributeError:
        return None


def _title_from_motion(motion: Motion) -> str:
    claim = (motion.claim or "").strip()
    mt = motion.motion_type
    if mt == "amend_tenet" and motion.target_ref:
        return f"Amend tenet {motion.target_ref}: {claim}"
    if mt == "remove_tenet" and motion.target_ref:
        return f"Remove tenet {motion.target_ref}: {claim}"
    if mt == "new_tenet":
        return f"New tenet: {claim}"
    if mt == "amend_rule" and motion.target_ref:
        kind = motion.target_kind or "rule"
        return f"Amend {kind} {motion.target_ref}: {claim}"
    if mt == "remove_rule" and motion.target_ref:
        kind = motion.target_kind or "rule"
        return f"Remove {kind} {motion.target_ref}: {claim}"
    if mt == "new_rule":
        return f"New rule: {claim}"
    if mt == "process":
        return f"Process: {claim}"
    return claim


def _motion_to_amendment(db: Session, motion: Motion) -> dict:
    filer = db.query(User).get(motion.filed_by_user_id) if motion.filed_by_user_id else None
    proposer = (filer.handle if filer and filer.handle else (filer.anon_id if filer else None))

    status = _STATUS_MAP.get(motion.status, motion.status)
    # cites_tenet stays as a tenet-specific surface for the existing
    # amendments frontend; rule/modifier targets are surfaced via the
    # title prefix instead.
    cites_tenet = motion.target_ref if motion.target_kind == "tenet" else None

    out: dict = {
        "id": f"motion-{motion.id:04d}",
        "status": status,
        "proposed_at": _format_date(motion.filed_at),
        "proposer": proposer,
        "cites_tenet": cites_tenet,
        "target_kind": motion.target_kind,
        "target_ref": motion.target_ref,
        "motion_type": motion.motion_type,
        "title": _title_from_motion(motion),
        "proposed_change": motion.reasoning,
        "novelty_argument": None,
        "soundness_argument": None,
        # Chamber URL is set for any non-filed motion. Filed motions
        # aren't open for deliberation yet; resolved motions keep the
        # link so the public record stays reachable.
        "discussion_url": (
            f"/motion-desk/deliberation-chamber/{motion.id}/"
            if motion.status != "filed" else None
        ),
        "co_sponsors": 0,
        "deliberation_opens_at": None,
        "deliberation_closes_at": None,
        "ratified_at": None,
        "rejected_at": None,
        "covered_by": None,
        "rejection_reason": None,
    }
    resolved_date = _format_date(motion.resolved_at)
    if status == "ratified":
        out["ratified_at"] = resolved_date
    elif status == "rejected":
        out["rejected_at"] = resolved_date
        out["rejection_reason"] = motion.resolution_summary
    elif status == "covered":
        out["rejected_at"] = resolved_date
        out["rejection_reason"] = motion.resolution_summary
        # If the motion explicitly cites a tenet, surface it as covered_by.
        out["covered_by"] = motion.target_ref if motion.target_kind == "tenet" else None
    return out


def load_amendment_log(db: Optional[Session] = None) -> dict:
    """Return the merged public amendment log.

    db is required to populate the live amendments list. Callers that
    only need the static config (rare; tests, migrations) may pass None
    and the amendments array stays empty.
    """
    config = dict(_load_log_config())
    amendments: list[dict] = []
    if db is not None:
        rows = (
            db.query(Motion)
            .filter(Motion.motion_type.in_(VALID_MOTION_TYPES))
            .order_by(Motion.filed_at.desc())
            .all()
        )
        amendments = [_motion_to_amendment(db, m) for m in rows]
    config["amendments"] = amendments
    return config
