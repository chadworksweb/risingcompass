import logging
from html import escape

import httpx
from fastapi import APIRouter, Depends, HTTPException, Header, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Optional

from app.database import get_db
from app.models import MisreadSubmission, MisreadBan
from app.schemas import (
    MisreadSubmissionCreate, MisreadSubmissionOut,
    MisreadStatusUpdate, MisreadBanOut,
)
from app.config import settings
from app.constants import COLOR_LABELS, COLOR_HEX, COLOR_BG

logger = logging.getLogger(__name__)

router = APIRouter(tags=["misread"])


def verify_admin_key(x_admin_key: str = Header(...)):
    if x_admin_key != settings.rc_admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")


def _send_receipt_email(submission: MisreadSubmission) -> bool:
    """Send a branded receipt email to the submitter via Resend. Fire-and-forget."""
    if not settings.resend_api_key:
        logger.warning("Resend not configured — skipping misread receipt email")
        return False

    color = submission.song_color or "green"
    tier_label = COLOR_LABELS.get(color, color)
    tier_hex = COLOR_HEX.get(color, "#888")
    tier_bg = COLOR_BG.get(color, "#f5f5f5")
    pos_str = ""

    html = f"""
    <div style="background:#ffffff;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;">
        <!-- Header -->
        <div style="background:#0a0a14;padding:24px 28px;border-radius:8px 8px 0 0;">
            <div style="font-size:11px;letter-spacing:0.3em;text-transform:uppercase;color:#00d4aa;margin-bottom:6px;">The Rising Compass</div>
            <div style="font-size:20px;font-weight:300;color:#eeeef4;">Misread Report Received</div>
        </div>

        <!-- Body -->
        <div style="padding:28px;">
            <p style="font-size:15px;color:#1a1a2e;line-height:1.6;margin:0 0 20px;">
                Hi {escape(submission.first_name)},
            </p>
            <p style="font-size:14px;color:#444;line-height:1.6;margin:0 0 24px;">
                We've received your report about the classification of the song below. Our team will review it. This is a confirmation only — no reply is needed or monitored.
            </p>

            <!-- Song card -->
            <div style="background:#f8f8fa;border:1px solid #eee;border-radius:6px;padding:16px 20px;margin-bottom:24px;">
                <div style="font-size:16px;font-weight:600;color:#1a1a2e;margin-bottom:4px;">{pos_str}{escape(submission.song_title)}</div>
                <div style="font-size:14px;color:#666;margin-bottom:10px;">{escape(submission.song_artist)}</div>
                <span style="display:inline-block;background:{tier_bg};color:{tier_hex};font-weight:600;font-size:12px;padding:3px 10px;border-radius:4px;">Current: {tier_label}</span>
            </div>

            <!-- Their message -->
            <div style="margin-bottom:24px;">
                <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#999;margin-bottom:6px;">Your Message</div>
                <div style="font-size:14px;color:#444;line-height:1.6;padding:12px 16px;background:#fafafa;border-left:3px solid #00d4aa;border-radius:0 4px 4px 0;">{escape(submission.message)}</div>
            </div>

            <p style="font-size:13px;color:#999;line-height:1.5;margin:0;">
                If the evidence supports a different reading, we'll update the classification. You won't receive a follow-up email — check the compass for any changes.
            </p>
        </div>

        <!-- Footer -->
        <div style="padding:16px 28px;border-top:1px solid #eee;text-align:center;">
            <span style="font-size:11px;color:#999;">The Rising Compass &middot; This is an automated receipt. Do not reply.</span>
        </div>
    </div>
    """

    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.email_from,
                "to": [submission.email],
                "reply_to": "noreply@risingcompass.net",
                "subject": f"Report Received — {submission.song_title}",
                "html": html,
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Misread receipt email sent to %s for submission #%s", submission.email, submission.id)
        return True
    except Exception:
        logger.exception("Failed to send misread receipt email via Resend")
        return False


def _is_banned(db: Session, device_id: Optional[str], ip: Optional[str]) -> bool:
    """Check if a device_id or IP is banned."""
    conditions = []
    if device_id:
        conditions.append(MisreadBan.device_id == device_id)
    if ip:
        conditions.append(MisreadBan.ip_address == ip)
    if not conditions:
        return False
    return db.query(MisreadBan).filter(or_(*conditions)).first() is not None


# --- Public Endpoints ---

@router.post("/api/misread", response_model=MisreadSubmissionOut, status_code=201)
def submit_misread(data: MisreadSubmissionCreate, request: Request, db: Session = Depends(get_db)):
    """Submit a misread classification report."""
    ip = request.client.host if request.client else None

    if _is_banned(db, data.device_id, ip):
        raise HTTPException(status_code=403, detail="You have been banned from submitting reports.")

    submission = MisreadSubmission(
        song_title=data.song_title,
        song_artist=data.song_artist,
        song_color=data.song_color,
        song_position=data.song_position,
        first_name=data.first_name,
        last_name=data.last_name,
        email=data.email,
        message=data.message,
        device_id=data.device_id,
        ip_address=ip,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    _send_receipt_email(submission)

    return submission


@router.get("/api/misread/check")
def check_ban(device_id: str = Query(default=""), request: Request = None, db: Session = Depends(get_db)):
    """Check if a device_id or IP is banned."""
    ip = request.client.host if request and request.client else None
    banned = _is_banned(db, device_id or None, ip)
    return {"banned": banned}


# --- Admin Endpoints ---

@router.get("/api/admin/misread", response_model=list[MisreadSubmissionOut], dependencies=[Depends(verify_admin_key)])
def list_misread(status: Optional[str] = None, db: Session = Depends(get_db)):
    """List all misread submissions, optionally filtered by status."""
    q = db.query(MisreadSubmission)
    if status:
        q = q.filter(MisreadSubmission.status == status)
    return q.order_by(MisreadSubmission.created_at.desc()).all()


@router.patch("/api/admin/misread/{submission_id}", response_model=MisreadSubmissionOut, dependencies=[Depends(verify_admin_key)])
def update_misread_status(submission_id: int, data: MisreadStatusUpdate, db: Session = Depends(get_db)):
    """Update the status of a misread submission."""
    submission = db.query(MisreadSubmission).filter(MisreadSubmission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    valid_statuses = {"pending", "reviewed", "accepted", "rejected", "flagged"}
    if data.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}")

    submission.status = data.status
    db.commit()
    db.refresh(submission)
    return submission


@router.post("/api/admin/misread/{submission_id}/ban", dependencies=[Depends(verify_admin_key)])
def flag_and_ban(submission_id: int, db: Session = Depends(get_db)):
    """Flag a submission and ban the submitter's device_id + IP."""
    submission = db.query(MisreadSubmission).filter(MisreadSubmission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    # Flag the submission
    submission.status = "flagged"

    # Create ban entry
    ban = MisreadBan(
        device_id=submission.device_id,
        ip_address=submission.ip_address,
        reason=f"Flagged from submission #{submission.id}",
    )
    db.add(ban)
    db.commit()
    db.refresh(submission)

    return {"detail": "Submission flagged and user banned", "submission_id": submission.id, "ban_device_id": ban.device_id, "ban_ip": ban.ip_address}


# --- Ban Management ---

@router.get("/api/admin/misread/bans", response_model=list[MisreadBanOut], dependencies=[Depends(verify_admin_key)])
def list_bans(db: Session = Depends(get_db)):
    """List all active bans."""
    return db.query(MisreadBan).order_by(MisreadBan.created_at.desc()).all()


@router.delete("/api/admin/misread/bans/{ban_id}", dependencies=[Depends(verify_admin_key)])
def remove_ban(ban_id: int, db: Session = Depends(get_db)):
    """Remove a ban."""
    ban = db.query(MisreadBan).filter(MisreadBan.id == ban_id).first()
    if not ban:
        raise HTTPException(status_code=404, detail="Ban not found")
    db.delete(ban)
    db.commit()
    return {"detail": "Ban removed"}
