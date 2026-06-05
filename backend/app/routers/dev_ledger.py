"""Dev Ledger router -- the "dev side, exposed".

Public (no X-Api-Key gate; the JWT is the authorization for writes, reads are
anonymous), under /api/dev-ledger:
  GET  /changelog        published, shipped, CalVer-stamped entries
  GET  /roadmap          public items grouped now / next / later
  GET  /feedback         public feature requests + bug reports (votable board)
  GET  /item/{id}        single public item
  GET  /version          current CalVer release id
  POST /feedback         submit a feature request / bug report (Clerk Tier 1)
  POST /vote/{id}        toggle a vote (Clerk Tier 1)

Admin (require_admin_session), under /api/admin/dev-ledger:
  GET   /                queue, all statuses incl. private
  POST  /                author a roadmap/changelog 'change' item
  PATCH /{id}            triage (status, stage, version, publish, resolve, ...)
  POST  /release         stamp items with a CalVer version -> public changelog

Walled from Motion Desk / Misread Reports / Deliberation Chamber: those govern
the tenet/framework layer; this is the product/engineering layer. See
RISING-COMPASS-DEV-LEDGER-SCOPE.md.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import optional_clerk_user, require_clerk_user
from app.database import get_db
from app.models import DevLedgerItem, User
from app.routers.admin import verify_admin_key
from app.routers.analyzer import limiter
from app.services import dev_ledger as dl

logger = logging.getLogger(__name__)

_STAGE_RANK = {"now": 0, "next": 1, "later": 2}


# ============================================================ public router

router = APIRouter(prefix="/api/dev-ledger", tags=["dev-ledger"])


class SubmitIn(BaseModel):
    item_type: str = Field(..., description="feature | bug")
    title: str = Field(..., min_length=dl.MIN_TITLE, max_length=dl.MAX_TITLE)
    body: str = Field(..., min_length=dl.MIN_BODY, max_length=dl.MAX_BODY)
    area: Optional[str] = Field(None, max_length=dl.MAX_AREA)
    severity: Optional[str] = Field(None, description="bugs only: low | med | high | critical")


@router.get("/changelog")
def get_changelog(
    version: Optional[str] = Query(None),
    area: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Published, shipped, CalVer-stamped entries, newest first. The frontend
    groups by version into release blocks."""
    q = db.query(DevLedgerItem).filter(
        DevLedgerItem.is_public.is_(True),
        DevLedgerItem.status == "shipped",
        DevLedgerItem.version.isnot(None),
    )
    if version:
        q = q.filter(DevLedgerItem.version == version)
    if area:
        q = q.filter(DevLedgerItem.area == area)
    rows = q.order_by(
        DevLedgerItem.shipped_at.desc().nullslast(),
        DevLedgerItem.id.desc(),
    ).limit(limit).all()
    return [dl.item_to_dict(db, r) for r in rows]


@router.get("/roadmap")
def get_roadmap(db: Session = Depends(get_db)):
    """Public, not-yet-shipped items that carry a stage, grouped now/next/later
    and ordered by vote_count within each bucket."""
    rows = (
        db.query(DevLedgerItem)
        .filter(
            DevLedgerItem.is_public.is_(True),
            DevLedgerItem.status != "shipped",
            DevLedgerItem.stage.isnot(None),
        )
        .all()
    )
    buckets: dict = {"now": [], "next": [], "later": []}
    for r in sorted(rows, key=lambda x: (-(x.vote_count or 0), x.id)):
        if r.stage in buckets:
            buckets[r.stage].append(dl.item_to_dict(db, r))
    return buckets


@router.get("/feedback")
def get_feedback(
    type: Optional[str] = Query(None, description="feature | bug"),
    sort: str = Query("top", description="top | new"),
    limit: int = Query(100, ge=1, le=300),
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(optional_clerk_user),
):
    """Public feature/bug board. Excludes shipped items (those live on the
    changelog). Sort by votes (top) or recency (new)."""
    q = db.query(DevLedgerItem).filter(
        DevLedgerItem.is_public.is_(True),
        DevLedgerItem.item_type.in_(("feature", "bug")),
        DevLedgerItem.status != "shipped",
    )
    if type in ("feature", "bug"):
        q = q.filter(DevLedgerItem.item_type == type)
    if sort == "new":
        q = q.order_by(DevLedgerItem.created_at.desc(), DevLedgerItem.id.desc())
    else:
        q = q.order_by(DevLedgerItem.vote_count.desc(), DevLedgerItem.id.desc())
    rows = q.limit(limit).all()
    viewer_id = viewer.id if viewer else None
    return [dl.item_to_dict(db, r, viewer_user_id=viewer_id) for r in rows]


@router.get("/item/{item_id}")
def get_item(
    item_id: int,
    db: Session = Depends(get_db),
    viewer: Optional[User] = Depends(optional_clerk_user),
):
    item = db.query(DevLedgerItem).get(item_id)
    if not item or not item.is_public:
        raise HTTPException(status_code=404, detail="Not found")
    viewer_id = viewer.id if viewer else None
    return dl.item_to_dict(db, item, viewer_user_id=viewer_id)


@router.get("/version")
def get_version(db: Session = Depends(get_db)):
    return {"version": dl.current_version(db), "changelog_url": "/dev/changelog"}


@router.post("/feedback", status_code=201)
@limiter.limit("10/hour")
async def submit_feedback(
    data: SubmitIn,
    request: Request,
    user: User = Depends(require_clerk_user),
    db: Session = Depends(get_db),
):
    """Submit a feature request or bug report. Lands private until an admin
    publishes it. Requires a Clerk Tier 1 account."""
    item = dl.create_submission(
        db,
        item_type=data.item_type,
        title=data.title,
        body=data.body,
        user=user,
        area=data.area,
        severity=data.severity,
    )
    return dl.item_to_dict(db, item, viewer_user_id=user.id)


@router.post("/vote/{item_id}")
@limiter.limit("60/hour")
async def toggle_vote(
    item_id: int,
    request: Request,
    user: User = Depends(require_clerk_user),
    db: Session = Depends(get_db),
):
    """Toggle the signed-in user's vote on a public feature/bug item."""
    item = db.query(DevLedgerItem).get(item_id)
    if not item or not item.is_public:
        raise HTTPException(status_code=404, detail="Not found")
    return dl.toggle_vote(db, item, user)


# ============================================================ admin router

admin_router = APIRouter(prefix="/api/admin/dev-ledger", tags=["dev-ledger-admin"])


class ChangeIn(BaseModel):
    title: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    stage: Optional[str] = Field(None, description="now | next | later")
    area: Optional[str] = Field(None, max_length=dl.MAX_AREA)


class TriageIn(BaseModel):
    status: Optional[str] = None
    stage: Optional[str] = None
    version: Optional[str] = None
    severity: Optional[str] = None
    area: Optional[str] = None
    is_public: Optional[bool] = None
    admin_note: Optional[str] = None
    resolution: Optional[str] = None
    duplicate_of_id: Optional[int] = None
    title: Optional[str] = None
    body: Optional[str] = None


class ReleaseIn(BaseModel):
    item_ids: list[int] = Field(..., min_length=1)
    version: str = Field(..., description="CalVer YYYY.MM.DD[suffix]")


@admin_router.get("")
def admin_list(
    status: Optional[str] = Query(None),
    item_type: Optional[str] = Query(None),
    public: Optional[bool] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    admin=Depends(verify_admin_key),
):
    """Triage queue: every item, all statuses, including private submissions."""
    q = db.query(DevLedgerItem)
    if status:
        q = q.filter(DevLedgerItem.status == status)
    if item_type:
        q = q.filter(DevLedgerItem.item_type == item_type)
    if public is not None:
        q = q.filter(DevLedgerItem.is_public.is_(public))
    rows = q.order_by(DevLedgerItem.created_at.desc(), DevLedgerItem.id.desc()).limit(limit).all()
    return [dl.item_to_dict(db, r, admin=True) for r in rows]


@admin_router.post("", status_code=201)
def admin_create_change(
    data: ChangeIn,
    db: Session = Depends(get_db),
    admin=Depends(verify_admin_key),
):
    """Author a roadmap/changelog 'change' item directly."""
    item = dl.create_change(
        db, title=data.title, body=data.body, stage=data.stage,
        area=data.area, admin_id=admin.id,
    )
    return dl.item_to_dict(db, item, admin=True)


@admin_router.patch("/{item_id}")
def admin_triage(
    item_id: int,
    data: TriageIn,
    db: Session = Depends(get_db),
    admin=Depends(verify_admin_key),
):
    item = db.query(DevLedgerItem).get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    item = dl.triage(
        db, item, admin_id=admin.id,
        status=data.status, stage=data.stage, version=data.version,
        severity=data.severity, area=data.area, is_public=data.is_public,
        admin_note=data.admin_note, resolution=data.resolution,
        duplicate_of_id=data.duplicate_of_id,
        title=data.title, body=data.body,
    )
    return dl.item_to_dict(db, item, admin=True)


@admin_router.delete("/{item_id}")
def admin_delete(
    item_id: int,
    db: Session = Depends(get_db),
    admin=Depends(verify_admin_key),
):
    """Hard-delete an item. Prefer unpublish to merely hide a public entry."""
    item = db.query(DevLedgerItem).get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    dl.delete_item(db, item)
    return {"deleted": item_id}


@admin_router.post("/release")
def admin_cut_release(
    data: ReleaseIn,
    db: Session = Depends(get_db),
    admin=Depends(verify_admin_key),
):
    """Stamp the chosen items with a CalVer version + ship + publish them in one
    action -- this is what moves work onto the public changelog."""
    items = dl.cut_release(db, item_ids=data.item_ids, version=data.version, admin_id=admin.id)
    return {"version": data.version, "count": len(items),
            "items": [dl.item_to_dict(db, i, admin=True) for i in items]}
