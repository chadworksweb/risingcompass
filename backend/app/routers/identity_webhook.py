"""Stripe Identity webhook handler.

Listens to identity.verification_session.* events and reconciles them
against the account_verifications row created by
POST /api/users/me/verify-identity. Only `verified` flips the user tier;
all other states (processing, canceled, requires_input) just update the
row so the admin / user can see progress.

Distinct from the donation webhook (/api/stripe-webhook). Uses
STRIPE_IDENTITY_WEBHOOK_SECRET so a signing-key compromise on one stream
doesn't forge events on the other.

Always returns 200 once the signature passes -- Stripe re-sends 5xx
events forever, and a malformed payload on our side shouldn't loop the
delivery queue.
"""

import logging
from datetime import datetime

import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import AccountVerification, User
from app.services import stripe_identity as stripe_identity_svc

logger = logging.getLogger(__name__)

router = APIRouter(tags=["identity-webhook"])


@router.post("/api/stripe-identity-webhook")
async def stripe_identity_webhook(
    request: Request,
    stripe_signature: str = Header(default=""),
):
    payload = await request.body()
    if not stripe_signature:
        raise HTTPException(400, "Missing Stripe-Signature header")

    try:
        event = stripe_identity_svc.construct_event(payload, stripe_signature)
    except RuntimeError as exc:
        logger.error("Stripe Identity webhook secret not configured: %s", exc)
        raise HTTPException(503, "Webhook not configured.")
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe Identity webhook signature verification failed")
        raise HTTPException(400, "Invalid signature")
    except Exception:
        logger.exception("Stripe Identity webhook parse failed")
        raise HTTPException(400, "Invalid payload")

    event_type = event.get("type", "")
    if not event_type.startswith("identity.verification_session."):
        # Not our concern -- 200 so Stripe doesn't retry.
        return {"ok": True, "ignored": event_type}

    obj = event.get("data", {}).get("object", {})
    session_id = obj.get("id")
    if not session_id:
        logger.warning("Stripe Identity event %s missing session id", event_type)
        return {"ok": True}

    db: Session = SessionLocal()
    try:
        av = (
            db.query(AccountVerification)
            .filter(AccountVerification.provider == "stripe_identity")
            .filter(AccountVerification.provider_reference == session_id)
            .first()
        )
        if av is None:
            # Possible if our POST persist failed but Stripe still fired the
            # webhook. Try the metadata.rc_user_id link as a fallback so we
            # don't lose the verification.
            rc_user_id = (obj.get("metadata") or {}).get("rc_user_id")
            if rc_user_id:
                try:
                    rc_user_id = int(rc_user_id)
                except (TypeError, ValueError):
                    rc_user_id = None
            if rc_user_id is None:
                logger.error("Stripe Identity webhook %s has no matching AV row and no rc_user_id metadata; session=%s",
                             event_type, session_id)
                return {"ok": True}
            av = AccountVerification(
                user_id=rc_user_id,
                provider="stripe_identity",
                provider_reference=session_id,
                status="requires_input",
            )
            db.add(av)
            db.flush()
            logger.warning("Reconciled missing AV row for session %s via metadata.rc_user_id=%s",
                           session_id, rc_user_id)

        now = datetime.utcnow()

        if event_type == "identity.verification_session.verified":
            av.status = "verified"
            av.verified_at = now
            av.failure_reason = None
            user = db.query(User).filter(User.id == av.user_id).first()
            if user is not None and user.tier != "id_verified":
                user.tier = "id_verified"
                logger.info("User %s elevated to id_verified via Stripe Identity %s",
                            user.id, session_id)
        elif event_type == "identity.verification_session.processing":
            av.status = "processing"
        elif event_type == "identity.verification_session.canceled":
            av.status = "canceled"
        elif event_type == "identity.verification_session.requires_input":
            av.status = "requires_input"
            # Stripe may include a `last_error` object with code + reason.
            last_error = obj.get("last_error") or {}
            reason = last_error.get("reason") or last_error.get("code")
            if reason:
                av.failure_reason = reason
        else:
            logger.info("Stripe Identity event %s ignored (session=%s)", event_type, session_id)

        db.commit()
    except Exception:
        logger.exception("Stripe Identity webhook handler crashed (event=%s)", event_type)
        db.rollback()
        # Swallow and 200 so Stripe doesn't replay forever on a persistent
        # local bug. Reconcile manually via dashboard if needed.
    finally:
        db.close()

    return {"ok": True}
