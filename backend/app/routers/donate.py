"""Lyrical Charger donation routes — Stripe Checkout + webhook.

Public endpoints (Lyrical Charger frontend):
  POST /api/donate             -> creates a Checkout session, persists a
                                  pending Donation row, returns session URL
  POST /api/stripe-webhook     -> Stripe signs webhook events with our
                                  per-endpoint secret; on
                                  checkout.session.completed we flip the
                                  Donation row to succeeded with the final
                                  amount + customer email

Donations are voluntary, non-refundable, and create no obligation. The
disclosure shows up next to the consent checkbox in the LC widget.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel, Field

from app.auth import verify_api_or_service_key
from app.database import SessionLocal
from app.models import Donation
from app.services.stripe_service import create_donation_session, construct_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["donate"])


# --- Schemas (kept inline; small surface, not reused elsewhere) -------------

class DonateIn(BaseModel):
    amount: float = Field(..., gt=0)
    source: str = Field("lyrical_charger", max_length=50)
    success_url: str = Field(..., max_length=500)
    cancel_url: str = Field(..., max_length=500)


class DonateOut(BaseModel):
    url: str
    session_id: str


# --- Origin allowlist for redirect URLs -------------------------------------
# Stripe uses success_url/cancel_url verbatim, so we only accept ones whose
# origin matches a known RC domain. Stops a hostile caller from using our
# Stripe checkout to redirect victims to an attacker site after payment.

_ALLOWED_RETURN_ORIGINS = {
    "https://risingcompass.net",
    "https://www.risingcompass.net",
    "http://localhost:3000",
    "http://localhost:3005",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3005",
}


def _origin_of(url: str) -> Optional[str]:
    from urllib.parse import urlparse
    try:
        u = urlparse(url)
        if not u.scheme or not u.netloc:
            return None
        return f"{u.scheme}://{u.netloc}"
    except Exception:
        logger.debug("donate: swallowed in _origin_of", exc_info=True)
        return None


def _validate_return_url(url: str) -> None:
    origin = _origin_of(url)
    if origin not in _ALLOWED_RETURN_ORIGINS:
        raise HTTPException(400, f"return URL origin not allowed: {origin}")


# --- POST /api/donate -------------------------------------------------------

@router.post("/api/donate", response_model=DonateOut,
             dependencies=[Depends(verify_api_or_service_key)])
async def create_donation(body: DonateIn, request: Request):
    """Create a Stripe Checkout session and persist a pending donation row.

    Always available -- the LC unavailable flag does NOT gate this. The
    whole point of the donate widget is that it stays reachable while
    the engine is down.
    """
    if body.amount < 1:
        raise HTTPException(400, "Minimum donation is $1.00")

    _validate_return_url(body.success_url)
    _validate_return_url(body.cancel_url)

    try:
        session = create_donation_session(
            amount_dollars=body.amount,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            source=body.source,
        )
    except RuntimeError as e:
        logger.error("Stripe not configured: %s", e)
        raise HTTPException(503, "Donations are not configured.")
    except stripe.error.StripeError as e:
        logger.exception("Stripe checkout session create failed")
        raise HTTPException(502, f"Payment processor error: {e.user_message or 'try again'}")

    if not session.url:
        raise HTTPException(500, "Stripe returned a session without a redirect URL.")

    db = SessionLocal()
    try:
        donation = Donation(
            stripe_session_id=session.id,
            amount_cents=int(session.amount_total or 0) or 0,
            currency=(session.currency or "usd"),
            status="pending",
            source=body.source[:50],
        )
        db.add(donation)
        db.commit()
    except Exception:
        # Persist failure is non-fatal -- Stripe is the source of truth.
        # The webhook can still upsert by stripe_session_id.
        logger.exception("Failed to persist pending Donation row")
        db.rollback()
    finally:
        db.close()

    return DonateOut(url=session.url, session_id=session.id)


# --- POST /api/stripe-webhook ----------------------------------------------

@router.post("/api/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(default="")):
    """Verify the Stripe webhook signature and update Donation rows.

    Listens to:
      - checkout.session.completed      -> succeeded
      - checkout.session.expired        -> expired
      - checkout.session.async_payment_failed -> failed
    """
    payload = await request.body()
    if not stripe_signature:
        raise HTTPException(400, "Missing Stripe-Signature header")

    try:
        event = construct_event(payload, stripe_signature)
    except RuntimeError as e:
        logger.error("Stripe webhook secret not configured: %s", e)
        raise HTTPException(503, "Webhook not configured.")
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe webhook signature verification failed")
        raise HTTPException(400, "Invalid signature")
    except Exception:
        logger.exception("Stripe webhook parse failed")
        raise HTTPException(400, "Invalid payload")

    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    session_id = obj.get("id")

    db = SessionLocal()
    try:
        donation = (
            db.query(Donation)
            .filter(Donation.stripe_session_id == session_id)
            .first()
        )

        if event_type == "checkout.session.completed":
            if not donation:
                # Webhook beat the persist on /api/donate -- upsert.
                donation = Donation(
                    stripe_session_id=session_id,
                    amount_cents=int(obj.get("amount_total") or 0),
                    currency=obj.get("currency") or "usd",
                    status="pending",
                    source=(obj.get("metadata") or {}).get("source"),
                )
                db.add(donation)
                db.flush()
            donation.status = "succeeded"
            donation.amount_cents = int(obj.get("amount_total") or donation.amount_cents)
            donation.currency = obj.get("currency") or donation.currency
            cd = obj.get("customer_details") or {}
            donation.customer_email = cd.get("email")
            donation.payment_intent_id = obj.get("payment_intent")
            donation.completed_at = datetime.now(timezone.utc)
            db.commit()
            logger.info("Donation %s succeeded: %d %s", donation.stripe_session_id,
                        donation.amount_cents, donation.currency)

        elif event_type in ("checkout.session.expired", "checkout.session.async_payment_failed"):
            if donation:
                donation.status = "expired" if event_type.endswith("expired") else "failed"
                donation.completed_at = datetime.now(timezone.utc)
                db.commit()

        # Other event types: ignore. We could log them, but Stripe re-sends
        # on 5xx so silent 200 is the friendliest response for noise.
    except Exception:
        logger.exception("Stripe webhook DB update failed")
        db.rollback()
        # Still return 200 so Stripe doesn't retry forever on a persistent
        # local failure. The Donation row can be reconciled manually from
        # the Stripe dashboard.
    finally:
        db.close()

    return {"received": True}
