"""General inquiry / contact form -- public submit + admin queue.

A reusable, account-free inbound form. The first caller is the Album
Charger's "need more than 15 tracks?" link, but the form is generic
(topic + subject + message). Bot-protected with the same honeypot +
Turnstile helper the Lyrical Charger uses, and rate-limited. New
submissions email the admin (moderation alert, pref-gated).
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import GeneralInquiry
from app.schemas import (
    GeneralInquiryCreate, GeneralInquiryOut, GeneralInquiryStatusUpdate,
    GeneralInquirySubmitOut,
)
from app.routers.admin import verify_admin_key
from app.routers.analyzer import _check_bot_protection, limiter
from app.services.alerts import emit_general_inquiry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["inquiries"])
# Admin endpoints live on their own router (cookie auth, no X-Api-Key).
admin_router = APIRouter(tags=["inquiries-admin"])

VALID_TOPICS = {"general", "album_charger", "bug", "partnership", "data", "other"}
VALID_STATUSES = {"new", "read", "closed"}


# --- Public ---

@router.post("/api/inquiry", response_model=GeneralInquirySubmitOut, status_code=201)
@limiter.limit("5/hour")
async def submit_inquiry(
    data: GeneralInquiryCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Submit a general inquiry. Honeypot + Turnstile protected; rate-limited."""
    await _check_bot_protection(data.hp_website, data.turnstile_token, request)

    message = (data.message or "").strip()
    if not message:
        raise HTTPException(422, "A message is required.")

    topic = (data.topic or "general").strip().lower()
    if topic not in VALID_TOPICS:
        topic = "other"

    ip = request.client.host if request.client else None
    inquiry = GeneralInquiry(
        name=(data.name or "").strip() or None,
        email=(data.email or "").strip() or None,
        topic=topic,
        subject=(data.subject or "").strip() or None,
        message=message,
        source=(data.source or "").strip()[:40] or None,
        page_url=(data.page_url or "").strip()[:500] or None,
        ip_address=ip,
        status="new",
    )
    db.add(inquiry)
    db.commit()
    db.refresh(inquiry)

    emit_general_inquiry(
        inquiry_id=inquiry.id, name=inquiry.name, email=inquiry.email,
        topic=inquiry.topic, subject=inquiry.subject, message=inquiry.message,
        source=inquiry.source,
    )

    return GeneralInquirySubmitOut(
        status="received",
        message="Thanks. We've got your message and will be in touch if a reply is needed.",
    )


# --- Admin ---

@admin_router.get("/api/admin/inquiries", response_model=list[GeneralInquiryOut],
                  dependencies=[Depends(verify_admin_key)])
def list_inquiries(
    status: Optional[str] = None,
    topic: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List inquiries, newest first, optionally filtered by status/topic."""
    q = db.query(GeneralInquiry)
    if status:
        q = q.filter(GeneralInquiry.status == status)
    if topic:
        q = q.filter(GeneralInquiry.topic == topic)
    return q.order_by(GeneralInquiry.created_at.desc()).all()


@admin_router.patch("/api/admin/inquiries/{inquiry_id}", response_model=GeneralInquiryOut,
                    dependencies=[Depends(verify_admin_key)])
def update_inquiry_status(inquiry_id: int, data: GeneralInquiryStatusUpdate,
                          db: Session = Depends(get_db)):
    """Mark an inquiry new / read / closed."""
    if data.status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. One of: {', '.join(sorted(VALID_STATUSES))}")
    inquiry = db.query(GeneralInquiry).filter(GeneralInquiry.id == inquiry_id).first()
    if not inquiry:
        raise HTTPException(404, "Inquiry not found")
    inquiry.status = data.status
    inquiry.handled_at = datetime.utcnow() if data.status != "new" else None
    db.commit()
    db.refresh(inquiry)
    return inquiry
