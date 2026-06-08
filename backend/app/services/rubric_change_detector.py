"""Auto-detect instrument-level changes from tenets/core.json.

The rubric is versioned at the item level: every tenet, every procedural rule,
the contamination modifier, and the top-level schema each carry a `version` +
`ratified_at`. A "rubric change" is therefore any (item_id, item_version) pair
that has never been logged. This runs at startup (after migrations) and writes
one rubric_changes row per newly-seen pair, stamped with the item's own
ratified_at so it sorts into the Calibration Log timeline at the real date.

First run against an existing DB logs every current item once (change_type
'added') -- the founding ratification history -- because none have been logged
yet. Subsequent edits to core.json that bump a version produce 'revised' rows;
an item that disappears from core.json produces a 'retired' row.

Fail-soft: never raises into startup. public_summary is left NULL for an admin
to enrich later -- the factual entry stands on its own.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import RubricChange
from app.services.agents.rubric_builder import load_tenets

logger = logging.getLogger(__name__)


def _parse_ratified(value: Optional[str]) -> Optional[datetime]:
    """Parse a core.json 'YYYY-MM-DD' ratified_at into a datetime."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _current_items(data: dict) -> list[dict]:
    """Flatten core.json into the versioned items we track, in the order they
    should read in the feed when same-dated (schema, tenets by tier, modifiers,
    rules)."""
    items: list[dict] = []

    # Top-level schema version.
    sv = data.get("schema_version")
    if sv:
        items.append({
            "item_kind": "schema",
            "item_id": "schema",
            "item_version": str(sv),
            "tier_slug": None,
            "title": f"Rubric schema v{sv}",
            "text": None,
            "ratified_at": _parse_ratified(data.get("ratified_at")),
        })

    # Tenets + tier notes, grouped under their tier.
    for tier in data.get("tiers", []):
        label = tier.get("label") or tier.get("slug")
        for tenet in tier.get("tenets", []):
            number = tenet.get("number", "")
            items.append({
                "item_kind": "tenet",
                "item_id": tenet["id"],
                "item_version": str(tenet.get("version", "1.0")),
                "tier_slug": tier.get("slug"),
                "title": f"{label} tenet {number}".strip(),
                "text": tenet.get("text"),
                "ratified_at": _parse_ratified(tenet.get("ratified_at")),
            })
        # Tier notes (e.g. violet-note-specifics). Only versioned notes are
        # tracked; an unversioned note is descriptive, not a logged change.
        for note in tier.get("notes", []):
            if not note.get("version"):
                continue
            items.append({
                "item_kind": "note",
                "item_id": note["id"],
                "item_version": str(note["version"]),
                "tier_slug": tier.get("slug"),
                "title": note.get("title") or note["id"],
                "text": note.get("text"),
                "ratified_at": _parse_ratified(note.get("ratified_at")),
            })

    # Contamination + any future modifiers.
    for mod in data.get("modifiers", []):
        body = "\n\n".join(p for p in [mod.get("definition"), mod.get("body")] if p)
        items.append({
            "item_kind": "modifier",
            "item_id": mod["id"],
            "item_version": str(mod.get("version", "1.0")),
            "tier_slug": None,
            "title": mod.get("label") or mod["id"],
            "text": body or None,
            "ratified_at": _parse_ratified(mod.get("ratified_at")),
        })

    # Parallel tags / flags (e.g. dogma_referenced). Modelled like modifiers
    # but kept in their own array so they don't render on the public Tenets
    # page or alter the agent prompt.
    for flag in data.get("flags", []):
        body = "\n\n".join(p for p in [flag.get("definition"), flag.get("body")] if p)
        items.append({
            "item_kind": "flag",
            "item_id": flag["id"],
            "item_version": str(flag.get("version", "1.0")),
            "tier_slug": None,
            "title": flag.get("label") or flag["id"],
            "text": body or None,
            "ratified_at": _parse_ratified(flag.get("ratified_at")),
        })

    # Procedural rules R1..Rn.
    for rule in data.get("rules", []):
        items.append({
            "item_kind": "rule",
            "item_id": rule["id"],
            "item_version": str(rule.get("version", "1.0")),
            "tier_slug": None,
            "title": rule.get("title") or rule["id"],
            "text": rule.get("text"),
            "ratified_at": _parse_ratified(rule.get("ratified_at")),
        })

    return items


def detect_rubric_changes(db: Session) -> int:
    """Write a rubric_changes row for every newly-seen (item_id, version) pair
    plus a 'retired' row for any previously-logged item now gone from core.json.
    Returns the number of rows written. Idempotent."""
    data = load_tenets()
    items = _current_items(data)
    schema_version = str(data.get("schema_version") or "")

    existing = db.query(RubricChange).all()
    seen_slugs = {r.change_slug for r in existing}
    # Latest logged row per item_id (ascending id = chronological insert order),
    # used for before_text on a revision and to detect a re-add after retire.
    latest_by_item: dict[str, RubricChange] = {}
    for r in sorted(existing, key=lambda x: x.id):
        latest_by_item[r.item_id] = r

    current_ids = {it["item_id"] for it in items}
    written = 0

    for it in items:
        slug = f"{it['item_id']}@{it['item_version']}"
        if slug in seen_slugs:
            continue
        prior = latest_by_item.get(it["item_id"])
        # 'added' if we've never logged this id (or its last state was retired);
        # 'revised' if a live prior version exists.
        if prior is None or prior.change_type == "retired":
            change_type = "added"
            before_text = None
        else:
            change_type = "revised"
            before_text = prior.after_text
        row = RubricChange(
            item_kind=it["item_kind"],
            item_id=it["item_id"],
            item_version=it["item_version"],
            change_type=change_type,
            tier_slug=it["tier_slug"],
            title=it["title"],
            before_text=before_text,
            after_text=it["text"],
            ratified_at=it["ratified_at"],
            schema_version=schema_version,
            change_slug=slug,
        )
        db.add(row)
        seen_slugs.add(slug)
        latest_by_item[it["item_id"]] = row
        written += 1

    # Retire: an id we've logged before that is no longer present and isn't
    # already in a retired state.
    for item_id, prior in list(latest_by_item.items()):
        if item_id in current_ids or prior.change_type == "retired":
            continue
        slug = f"{item_id}@retired:{prior.item_version}"
        if slug in seen_slugs:
            continue
        row = RubricChange(
            item_kind=prior.item_kind,
            item_id=item_id,
            item_version=prior.item_version,
            change_type="retired",
            tier_slug=prior.tier_slug,
            title=prior.title,
            before_text=prior.after_text,
            after_text=None,
            ratified_at=None,
            schema_version=schema_version,
            change_slug=slug,
        )
        db.add(row)
        seen_slugs.add(slug)
        written += 1

    if written:
        db.commit()
        logger.info(
            "rubric_change_detector: wrote %d new entr%s",
            written, "y" if written == 1 else "ies",
        )
    return written
