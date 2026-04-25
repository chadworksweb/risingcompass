"""Artist Verified -- public lead form + admin funnel/block management.

Public surface (X-Api-Key required):
  POST /api/artist-verification/inquiry  -- inbound 'are you the artist?' lead
  GET  /api/artist-verification/block    -- fetch published block for a song

Admin surface (admin session cookie required):
  GET    /api/admin/artist-verification/inquiries
  PATCH  /api/admin/artist-verification/inquiries/{id}
  GET    /api/admin/artist-verification/funnel
  GET    /api/admin/artist-verification/artists/{artist_id}
  PUT    /api/admin/artist-verification/artists/{artist_id}/verification
  POST   /api/admin/artist-verification/artists/{artist_id}/blocks
  PATCH  /api/admin/artist-verification/blocks/{block_id}
  DELETE /api/admin/artist-verification/blocks/{block_id}
  GET    /api/admin/artist-verification/artists-search

Deepfake gate: when verification_method == 'video_call', funnel_stage cannot
move to 'active' and no block can be published until all five deepfake
checklist booleans are true. Other methods skip the gate but still require
verification_method to be set before stage='active'.
"""

import logging
from datetime import datetime
from html import escape
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (
    Artist,
    ArtistVerification,
    ArtistVerificationBlock,
    ArtistVerificationInquiry,
)
from app.routers.admin import verify_admin_key
from app.routers.analyzer import _check_bot_protection
from app.schemas import (
    ArtistVerificationBlockCreate,
    ArtistVerificationBlockOut,
    ArtistVerificationBlockUpdate,
    ArtistVerificationDetailOut,
    ArtistVerificationFunnelArtistOut,
    ArtistVerificationInquiryCreate,
    ArtistVerificationInquiryOut,
    ArtistVerificationInquiryStatusUpdate,
    ArtistVerificationOut,
    ArtistVerificationUpdate,
    ArtistVerifiedBlockPublic,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["artist-verification"])
admin_router = APIRouter(tags=["artist-verification-admin"])

VALID_STAGES = {"lead", "contacted", "in_conversation", "active"}
VALID_METHODS = {"in_person", "video_call", "audio_call", "prior_relationship"}
VALID_INQUIRY_STATUSES = {"pending", "contacted", "dismissed", "promoted"}


def _deepfake_complete(v: ArtistVerification) -> bool:
    return all([
        v.deepfake_live_challenge_passed,
        v.deepfake_cross_channel_confirmed,
        v.deepfake_two_sessions_completed,
        v.deepfake_reference_match_confirmed,
        v.deepfake_recording_archived,
    ])


def _gate_passes(v: ArtistVerification) -> bool:
    """True when this artist is allowed to be at funnel_stage='active' and
    have published verification blocks. Video calls require the deepfake
    checklist; other methods just need the method to be set."""
    if v.verification_method is None:
        return False
    if v.verification_method == "video_call":
        return _deepfake_complete(v)
    return True


# --- Email (Resend) ---

def _send_receipt_email(inquiry: ArtistVerificationInquiry) -> bool:
    """Confirmation back to the claimant. Fire-and-forget — failures are
    logged but never block the submission response."""
    if not settings.resend_api_key:
        logger.warning("Resend not configured — skipping artist-verification receipt email")
        return False

    proof_block = ""
    if inquiry.proof_links:
        proof_block = (
            '<div style="margin-bottom:24px;">'
            '<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#999;margin-bottom:6px;">Proof Links You Sent</div>'
            f'<div style="font-size:14px;color:#444;line-height:1.6;padding:12px 16px;background:#fafafa;border-left:3px solid #00d4aa;border-radius:0 4px 4px 0;white-space:pre-wrap;">{escape(inquiry.proof_links)}</div>'
            '</div>'
        )

    html = f"""
    <div style="background:#ffffff;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;">
        <div style="background:#0a0a14;padding:24px 28px;border-radius:8px 8px 0 0;">
            <div style="font-size:11px;letter-spacing:0.3em;text-transform:uppercase;color:#00d4aa;margin-bottom:6px;">The Rising Compass</div>
            <div style="font-size:20px;font-weight:300;color:#eeeef4;">Artist Inquiry Received</div>
        </div>
        <div style="padding:28px;">
            <p style="font-size:15px;color:#1a1a2e;line-height:1.6;margin:0 0 20px;">
                Hi {escape(inquiry.claimant_name)},
            </p>
            <p style="font-size:14px;color:#444;line-height:1.6;margin:0 0 24px;">
                We received your &ldquo;Are you the artist?&rdquo; inquiry for the song below. Verification on The Rising Compass happens face-to-face or by video call with our anti-deepfake protocol &mdash; we&rsquo;ll review what you&rsquo;ve sent and reach out to set up a session if it checks out. This confirmation is automated; no reply needed.
            </p>
            <div style="background:#f8f8fa;border:1px solid #eee;border-radius:6px;padding:16px 20px;margin-bottom:24px;">
                <div style="font-size:16px;font-weight:600;color:#1a1a2e;margin-bottom:4px;">{escape(inquiry.song_title)}</div>
                <div style="font-size:14px;color:#666;">{escape(inquiry.song_artist)}</div>
            </div>
            <div style="margin-bottom:24px;">
                <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#999;margin-bottom:6px;">Your Message</div>
                <div style="font-size:14px;color:#444;line-height:1.6;padding:12px 16px;background:#fafafa;border-left:3px solid #00d4aa;border-radius:0 4px 4px 0;white-space:pre-wrap;">{escape(inquiry.message)}</div>
            </div>
            {proof_block}
            <p style="font-size:13px;color:#999;line-height:1.5;margin:0;">
                If we move forward, you&rsquo;ll hear from us at this address.
            </p>
        </div>
        <div style="padding:16px 28px;border-top:1px solid #eee;text-align:center;">
            <span style="font-size:11px;color:#999;">The Rising Compass &middot; Automated receipt &mdash; do not reply.</span>
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
                "to": [inquiry.claimant_email],
                "reply_to": "noreply@risingcompass.net",
                "subject": f"Artist Inquiry Received — {inquiry.song_title}",
                "html": html,
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info(
            "Artist-verification receipt sent to %s for inquiry #%s",
            inquiry.claimant_email, inquiry.id,
        )
        return True
    except Exception:
        logger.exception("Failed to send artist-verification receipt email")
        return False


def _send_admin_notification_email(inquiry: ArtistVerificationInquiry) -> bool:
    """Notify the admin inbox that a new artist-verification inquiry arrived.
    Same recipient as misread notifications (settings.misread_notify_email)."""
    if not settings.resend_api_key or not settings.misread_notify_email:
        logger.info(
            "Artist-verification admin notify skipped — resend_api_key or misread_notify_email unset"
        )
        return False

    role = inquiry.claimant_role or "(not specified)"
    proof_line = ""
    if inquiry.proof_links:
        proof_line = (
            f"<p><strong>Proof links:</strong><br><span style='white-space:pre-wrap;'>{escape(inquiry.proof_links)}</span></p>"
        )
    song_ref = ""
    if inquiry.song_source and inquiry.song_id:
        song_ref = f" [{escape(inquiry.song_source)}/{inquiry.song_id}]"

    html = f"""
    <div style="font-family:'Inter',-apple-system,sans-serif;max-width:600px;color:#1a1a2e;">
      <h2 style="margin:0 0 12px;">New Artist Verification Inquiry &mdash; #{inquiry.id}</h2>
      <p><strong>Song:</strong> {escape(inquiry.song_title)} &mdash; {escape(inquiry.song_artist)}{song_ref}</p>
      <p><strong>From:</strong> {escape(inquiry.claimant_name)} &lt;{escape(inquiry.claimant_email)}&gt; &middot; <em>{escape(role)}</em></p>
      <p><strong>Message:</strong><br><span style='white-space:pre-wrap;'>{escape(inquiry.message)}</span></p>
      {proof_line}
      <p style="color:#666;font-size:13px;">Triage in admin dashboard &rarr; Artist Verified tab.</p>
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
                "to": [settings.misread_notify_email],
                "subject": f"[RC] New Artist Inquiry: {inquiry.song_title} — {inquiry.song_artist}",
                "html": html,
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info(
            "Artist-verification admin notify sent to %s for inquiry #%s",
            settings.misread_notify_email, inquiry.id,
        )
        return True
    except Exception:
        logger.exception("Failed to send artist-verification admin notify email")
        return False


# --- Public ---

@router.post(
    "/api/artist-verification/inquiry",
    response_model=ArtistVerificationInquiryOut,
    status_code=201,
)
async def submit_inquiry(
    data: ArtistVerificationInquiryCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Submit an inbound 'are you the artist?' lead from a song page.

    Bot-protected via honeypot + (optional) Cloudflare Turnstile — the same
    helper the Lyrical Charger uses. No throttle on legitimate humans; this
    is a relationship form, not a high-volume one.
    """
    await _check_bot_protection(data.hp_website, data.turnstile_token, request)
    ip = request.client.host if request.client else None
    inquiry = ArtistVerificationInquiry(
        song_title=data.song_title,
        song_artist=data.song_artist,
        song_source=data.song_source,
        song_id=data.song_id,
        song_color=data.song_color,
        song_position=data.song_position,
        claimant_name=data.claimant_name,
        claimant_email=data.claimant_email,
        claimant_role=data.claimant_role,
        proof_links=data.proof_links,
        message=data.message,
        device_id=data.device_id,
        ip_address=ip,
    )
    db.add(inquiry)
    db.commit()
    db.refresh(inquiry)

    _send_receipt_email(inquiry)
    _send_admin_notification_email(inquiry)

    return inquiry


@router.get("/api/artist-verification/block")
def get_published_block(
    song_source: str = Query(..., max_length=20),
    song_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Return the published verification block for a song, or null when none.

    If multiple verified artists each published a block on the same song
    (collab), the most recently published wins for v1.
    """
    row = (
        db.query(ArtistVerificationBlock, Artist)
        .join(Artist, Artist.id == ArtistVerificationBlock.artist_id)
        .filter(
            ArtistVerificationBlock.song_source == song_source,
            ArtistVerificationBlock.song_id == song_id,
            ArtistVerificationBlock.published == True,  # noqa: E712
        )
        .order_by(ArtistVerificationBlock.published_at.desc())
        .first()
    )
    if not row:
        return None
    block, artist = row
    return ArtistVerifiedBlockPublic(
        artist_id=artist.id,
        artist_name=artist.name,
        artist_slug=artist.slug,
        block_text=block.block_text,
        video_url=block.video_url,
        audio_url=block.audio_url,
        published_at=block.published_at,
    )


# --- Admin: inquiries ---

@admin_router.get(
    "/api/admin/artist-verification/inquiries",
    response_model=list[ArtistVerificationInquiryOut],
    dependencies=[Depends(verify_admin_key)],
)
def list_inquiries(status: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(ArtistVerificationInquiry)
    if status:
        if status not in VALID_INQUIRY_STATUSES:
            raise HTTPException(
                400,
                f"Invalid status. Must be one of: {sorted(VALID_INQUIRY_STATUSES)}",
            )
        q = q.filter(ArtistVerificationInquiry.status == status)
    return q.order_by(ArtistVerificationInquiry.created_at.desc()).all()


@admin_router.patch(
    "/api/admin/artist-verification/inquiries/{inquiry_id}",
    response_model=ArtistVerificationInquiryOut,
    dependencies=[Depends(verify_admin_key)],
)
def update_inquiry(
    inquiry_id: int,
    data: ArtistVerificationInquiryStatusUpdate,
    db: Session = Depends(get_db),
):
    """Update inquiry status. Promoting requires artist_id; we ensure an
    ArtistVerification row exists at stage='lead' on that artist."""
    inq = (
        db.query(ArtistVerificationInquiry)
        .filter(ArtistVerificationInquiry.id == inquiry_id)
        .first()
    )
    if not inq:
        raise HTTPException(404, "Inquiry not found")
    if data.status not in VALID_INQUIRY_STATUSES:
        raise HTTPException(
            400,
            f"Invalid status. Must be one of: {sorted(VALID_INQUIRY_STATUSES)}",
        )

    if data.status == "promoted":
        if not data.artist_id:
            raise HTTPException(400, "Promoting an inquiry requires artist_id")
        artist = db.query(Artist).filter(Artist.id == data.artist_id).first()
        if not artist:
            raise HTTPException(404, f"Artist {data.artist_id} not found")
        v = (
            db.query(ArtistVerification)
            .filter(ArtistVerification.artist_id == artist.id)
            .first()
        )
        if not v:
            v = ArtistVerification(artist_id=artist.id, funnel_stage="lead")
            db.add(v)
        inq.artist_id = artist.id

    inq.status = data.status
    db.commit()
    db.refresh(inq)
    return inq


# --- Admin: funnel + artist detail ---

@admin_router.get(
    "/api/admin/artist-verification/funnel",
    response_model=list[ArtistVerificationFunnelArtistOut],
    dependencies=[Depends(verify_admin_key)],
)
def funnel_list(stage: Optional[str] = None, db: Session = Depends(get_db)):
    """List artists with verification records, optionally filtered by stage."""
    if stage and stage not in VALID_STAGES:
        raise HTTPException(
            400, f"Invalid stage. Must be one of: {sorted(VALID_STAGES)}"
        )

    rows = (
        db.query(ArtistVerification, Artist)
        .join(Artist, Artist.id == ArtistVerification.artist_id)
        .order_by(ArtistVerification.updated_at.desc())
        .all()
    )

    block_counts: dict[int, dict[str, int]] = {}
    for aid, total, published in (
        db.query(
            ArtistVerificationBlock.artist_id,
            func.count(ArtistVerificationBlock.id),
            func.sum(case((ArtistVerificationBlock.published == True, 1), else_=0)),  # noqa: E712
        ).group_by(ArtistVerificationBlock.artist_id)
    ):
        block_counts[aid] = {"total": total or 0, "published": int(published or 0)}

    out = []
    for v, a in rows:
        if stage and v.funnel_stage != stage:
            continue
        bc = block_counts.get(a.id, {"total": 0, "published": 0})
        out.append(
            ArtistVerificationFunnelArtistOut(
                artist_id=a.id,
                artist_name=a.name,
                artist_slug=a.slug,
                verification_id=v.id,
                funnel_stage=v.funnel_stage,
                verification_method=v.verification_method,
                block_count=bc["total"],
                published_block_count=bc["published"],
                contacted_at=v.contacted_at,
                verified_at=v.verified_at,
                updated_at=v.updated_at,
            )
        )
    return out


@admin_router.get(
    "/api/admin/artist-verification/artists/{artist_id}",
    response_model=ArtistVerificationDetailOut,
    dependencies=[Depends(verify_admin_key)],
)
def artist_detail(artist_id: int, db: Session = Depends(get_db)):
    artist = db.query(Artist).filter(Artist.id == artist_id).first()
    if not artist:
        raise HTTPException(404, "Artist not found")
    v = (
        db.query(ArtistVerification)
        .filter(ArtistVerification.artist_id == artist_id)
        .first()
    )
    blocks = (
        db.query(ArtistVerificationBlock)
        .filter(ArtistVerificationBlock.artist_id == artist_id)
        .order_by(ArtistVerificationBlock.created_at.desc())
        .all()
    )
    return ArtistVerificationDetailOut(
        artist_id=artist.id,
        artist_name=artist.name,
        artist_slug=artist.slug,
        verification=v,
        blocks=blocks,
    )


@admin_router.put(
    "/api/admin/artist-verification/artists/{artist_id}/verification",
    response_model=ArtistVerificationOut,
    dependencies=[Depends(verify_admin_key)],
)
def upsert_verification(
    artist_id: int,
    data: ArtistVerificationUpdate,
    db: Session = Depends(get_db),
):
    """Create or update the verification record for an artist. Enforces the
    deepfake gate when funnel_stage='active' is requested."""
    artist = db.query(Artist).filter(Artist.id == artist_id).first()
    if not artist:
        raise HTTPException(404, "Artist not found")

    v = (
        db.query(ArtistVerification)
        .filter(ArtistVerification.artist_id == artist_id)
        .first()
    )
    if not v:
        v = ArtistVerification(artist_id=artist_id, funnel_stage="lead")
        db.add(v)
        db.flush()

    update_data = data.model_dump(exclude_unset=True)

    if "funnel_stage" in update_data:
        new_stage = update_data["funnel_stage"]
        if new_stage not in VALID_STAGES:
            raise HTTPException(
                400, f"Invalid stage. Must be one of: {sorted(VALID_STAGES)}"
            )

    if "verification_method" in update_data:
        m = update_data["verification_method"]
        if m is not None and m not in VALID_METHODS:
            raise HTTPException(
                400, f"Invalid method. Must be one of: {sorted(VALID_METHODS)}"
            )

    for k, val in update_data.items():
        setattr(v, k, val)

    if v.funnel_stage in {"contacted", "in_conversation", "active"} and v.contacted_at is None:
        v.contacted_at = datetime.utcnow()

    if v.funnel_stage == "active":
        if not _gate_passes(v):
            raise HTTPException(
                400,
                "Cannot move to active. video_call requires all five deepfake "
                "checklist items + verification_method set; other methods "
                "require verification_method set.",
            )
        if v.verified_at is None:
            v.verified_at = datetime.utcnow()

    db.commit()
    db.refresh(v)
    return v


# --- Admin: blocks ---

@admin_router.post(
    "/api/admin/artist-verification/artists/{artist_id}/blocks",
    response_model=ArtistVerificationBlockOut,
    status_code=201,
    dependencies=[Depends(verify_admin_key)],
)
def create_block(
    artist_id: int,
    data: ArtistVerificationBlockCreate,
    db: Session = Depends(get_db),
):
    artist = db.query(Artist).filter(Artist.id == artist_id).first()
    if not artist:
        raise HTTPException(404, "Artist not found")
    v = (
        db.query(ArtistVerification)
        .filter(ArtistVerification.artist_id == artist_id)
        .first()
    )

    if data.published:
        if not v or v.funnel_stage != "active":
            raise HTTPException(
                400, "Cannot publish: artist must be at funnel_stage='active'."
            )
        if not _gate_passes(v):
            raise HTTPException(400, "Cannot publish: deepfake gate not satisfied.")

    existing = (
        db.query(ArtistVerificationBlock)
        .filter(
            ArtistVerificationBlock.artist_id == artist_id,
            ArtistVerificationBlock.song_source == data.song_source,
            ArtistVerificationBlock.song_id == data.song_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            409,
            f"Block already exists for this artist + song (id={existing.id}). "
            f"Use PATCH to update.",
        )

    block = ArtistVerificationBlock(
        artist_id=artist_id,
        song_source=data.song_source,
        song_id=data.song_id,
        block_text=data.block_text,
        video_url=data.video_url,
        audio_url=data.audio_url,
        internal_notes=data.internal_notes,
        published=data.published,
        published_at=datetime.utcnow() if data.published else None,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


@admin_router.patch(
    "/api/admin/artist-verification/blocks/{block_id}",
    response_model=ArtistVerificationBlockOut,
    dependencies=[Depends(verify_admin_key)],
)
def update_block(
    block_id: int,
    data: ArtistVerificationBlockUpdate,
    db: Session = Depends(get_db),
):
    block = (
        db.query(ArtistVerificationBlock)
        .filter(ArtistVerificationBlock.id == block_id)
        .first()
    )
    if not block:
        raise HTTPException(404, "Block not found")

    update_data = data.model_dump(exclude_unset=True)

    # Gate publish toggle
    if update_data.get("published") is True and not block.published:
        v = (
            db.query(ArtistVerification)
            .filter(ArtistVerification.artist_id == block.artist_id)
            .first()
        )
        if not v or v.funnel_stage != "active":
            raise HTTPException(
                400, "Cannot publish: artist must be at funnel_stage='active'."
            )
        if not _gate_passes(v):
            raise HTTPException(400, "Cannot publish: deepfake gate not satisfied.")
        block.published_at = datetime.utcnow()
    elif update_data.get("published") is False:
        block.published_at = None

    for k, val in update_data.items():
        setattr(block, k, val)

    db.commit()
    db.refresh(block)
    return block


@admin_router.delete(
    "/api/admin/artist-verification/blocks/{block_id}",
    dependencies=[Depends(verify_admin_key)],
)
def delete_block(block_id: int, db: Session = Depends(get_db)):
    block = (
        db.query(ArtistVerificationBlock)
        .filter(ArtistVerificationBlock.id == block_id)
        .first()
    )
    if not block:
        raise HTTPException(404, "Block not found")
    db.delete(block)
    db.commit()
    return {"detail": "Block deleted"}


@admin_router.get(
    "/api/admin/artist-verification/artists-search",
    dependencies=[Depends(verify_admin_key)],
)
def search_artists(
    q: str = Query(..., min_length=1, max_length=200),
    db: Session = Depends(get_db),
):
    """Search artists by name for the inquiry-promote dropdown."""
    results = (
        db.query(Artist)
        .filter(Artist.name.ilike(f"%{q}%"))
        .order_by(Artist.name)
        .limit(20)
        .all()
    )
    return [{"id": a.id, "name": a.name, "slug": a.slug} for a in results]
