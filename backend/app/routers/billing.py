"""Billing routes — subscription + credit-pack Checkout, wallet, estimate,
and the Stripe billing webhook.

Public endpoints (Clerk-authed):
  POST /api/billing/checkout/subscription -> Plus/Pro subscription Checkout
  POST /api/billing/checkout/pack         -> credit pack one-time Checkout
  GET  /api/billing/me                    -> wallet snapshot (tier + buckets)
  POST /api/billing/estimate              -> word/track count -> credit cost

Public endpoint (Stripe -> RC, signed):
  POST /api/billing-webhook               -> tier flips + credit grants

Webhook event vocabulary:
  checkout.session.completed
    mode=subscription -> set subscription_tier + first monthly_grant allowance
    mode=payment      -> pack_purchase grant into purchased bucket
  invoice.paid        -> monthly_grant: reset+grant allowance for the period
  customer.subscription.updated -> tier/status/period_end sync
  customer.subscription.deleted -> tier=free, zero allowance, keep purchased

Idempotency: every grant_credits call carries (reason, ref_id=event.id) so
a replayed webhook is a no-op (credit_ledger UNIQUE partial index). Tier
flips on subscription.updated/deleted are not gated through the ledger;
they're property updates that converge on the same target state regardless
of replay order.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app import billing_config
from app.auth import require_clerk_user
from app.config import settings
from app.database import SessionLocal
from app.models import CreditLedger, User
from app.services import billing as billing_svc
from app.services import posthog_analytics
from app.services.feature_flags import is_launch_locked, launch_locked_message
from app.services.stripe_service import (
    construct_billing_event,
    create_credit_pack_session,
    create_subscription_session,
    fetch_subscription,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["billing"])


# --- Return URL allowlist (mirrors donate.py) -----------------------------
_ALLOWED_RETURN_ORIGINS = {
    "https://risingcompass.net",
    "https://www.risingcompass.net",
    "http://localhost:3000",
    "http://localhost:3005",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3005",
}


def _check_launch_open_or_503() -> None:
    """Block billing checkout while the pre-launch gate is locked. The sign-up
    form + buttons are faded in the UI, but this is the real money-safety gate:
    on live Stripe a stale tab or direct call must not create a charge."""
    db = SessionLocal()
    try:
        if is_launch_locked(db):
            raise HTTPException(
                status_code=503,
                detail={"error": "launch_locked", "message": launch_locked_message(db)},
            )
    finally:
        db.close()


def _validate_return_url(url: str) -> None:
    try:
        u = urlparse(url)
        if not u.scheme or not u.netloc:
            raise HTTPException(400, "return URL malformed")
        origin = f"{u.scheme}://{u.netloc}"
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "return URL malformed")
    if origin not in _ALLOWED_RETURN_ORIGINS:
        raise HTTPException(400, f"return URL origin not allowed: {origin}")


# --- Schemas ---------------------------------------------------------------

class SubscriptionCheckoutIn(BaseModel):
    tier: str = Field(..., description="plus | pro")
    success_url: Optional[str] = Field(None, max_length=500)
    cancel_url: Optional[str] = Field(None, max_length=500)


class PackCheckoutIn(BaseModel):
    pack: str = Field(..., description="pack_25 | pack_100 | pack_300")
    success_url: Optional[str] = Field(None, max_length=500)
    cancel_url: Optional[str] = Field(None, max_length=500)


def _resolve_return_urls(success_url: Optional[str], cancel_url: Optional[str]) -> tuple[str, str]:
    """Resolve the checkout return URLs, falling back to the configured
    STRIPE_BILLING_RETURN_URL when the caller omits them. Both are validated
    against the origin allowlist."""
    success = success_url or settings.stripe_billing_return_url
    cancel = cancel_url or settings.stripe_billing_return_url
    if not success or not cancel:
        raise HTTPException(400, "success_url/cancel_url required (no billing return URL configured)")
    _validate_return_url(success)
    _validate_return_url(cancel)
    return success, cancel


class CheckoutOut(BaseModel):
    url: str
    session_id: str


class EstimateIn(BaseModel):
    word_count: Optional[int] = Field(None, ge=0)
    track_count: Optional[int] = Field(None, ge=0)


class EstimateOut(BaseModel):
    credits: int
    breakdown: dict


# --- POST /api/billing/checkout/subscription ------------------------------

@router.post("/api/billing/checkout/subscription", response_model=CheckoutOut)
async def create_subscription_checkout(
    body: SubscriptionCheckoutIn,
    user: User = Depends(require_clerk_user),
):
    """Start a Plus or Pro subscription Checkout. checkout.session.completed
    syncs the tier; invoice.paid grants the period's allowance (single
    grant authority)."""
    _check_launch_open_or_503()
    tier = billing_config.TIERS.get(body.tier)
    if not tier or tier.key == "free":
        raise HTTPException(400, "Unknown subscription tier")
    price_id = tier.stripe_price_id
    if not price_id:
        raise HTTPException(503, f"Stripe price ID not configured for tier {tier.key}")

    success_url, cancel_url = _resolve_return_urls(body.success_url, body.cancel_url)

    try:
        session = create_subscription_session(
            price_id=price_id,
            user_id=user.id,
            success_url=success_url,
            cancel_url=cancel_url,
            customer_id=user.stripe_customer_id,
            tier_key=tier.key,
        )
    except RuntimeError as e:
        logger.error("Stripe not configured: %s", e)
        raise HTTPException(503, "Billing is not configured.")
    except stripe.error.StripeError as e:
        logger.exception("Subscription checkout create failed")
        raise HTTPException(502, f"Payment processor error: {getattr(e, 'user_message', None) or 'try again'}")

    if not session.url:
        raise HTTPException(500, "Stripe returned a session without a redirect URL.")
    return CheckoutOut(url=session.url, session_id=session.id)


# --- POST /api/billing/checkout/pack --------------------------------------

@router.post("/api/billing/checkout/pack", response_model=CheckoutOut)
async def create_pack_checkout(
    body: PackCheckoutIn,
    user: User = Depends(require_clerk_user),
):
    """Start a one-time credit-pack Checkout. The webhook grants credits to
    the user's purchased bucket on checkout.session.completed."""
    _check_launch_open_or_503()
    pack = billing_config.PACKS.get(body.pack)
    if not pack:
        raise HTTPException(400, "Unknown pack")
    price_id = pack.stripe_price_id
    if not price_id:
        raise HTTPException(503, f"Stripe price ID not configured for pack {pack.key}")

    success_url, cancel_url = _resolve_return_urls(body.success_url, body.cancel_url)

    try:
        session = create_credit_pack_session(
            price_id=price_id,
            pack_key=pack.key,
            credits=pack.credits,
            user_id=user.id,
            success_url=success_url,
            cancel_url=cancel_url,
            customer_id=user.stripe_customer_id,
        )
    except RuntimeError as e:
        logger.error("Stripe not configured: %s", e)
        raise HTTPException(503, "Billing is not configured.")
    except stripe.error.StripeError as e:
        logger.exception("Pack checkout create failed")
        raise HTTPException(502, f"Payment processor error: {getattr(e, 'user_message', None) or 'try again'}")

    if not session.url:
        raise HTTPException(500, "Stripe returned a session without a redirect URL.")
    return CheckoutOut(url=session.url, session_id=session.id)


# --- GET /api/billing/me --------------------------------------------------

@router.get("/api/billing/me")
async def billing_me(user: User = Depends(require_clerk_user)):
    """Wallet snapshot for the signed-in user. Surfaces tier, status,
    next renewal, and the two-bucket credit balance."""
    snap = billing_svc.wallet_snapshot(user)
    snap["tier_label"] = billing_config.TIERS.get(snap["tier"], billing_config.TIER_FREE).label
    snap["is_paid"] = billing_config.is_paid_user(snap["tier"])
    # Free-tier daily-charge allotment (used / remaining / reset). Paid users
    # report a zero allotment; the UI only shows the line when eligible.
    snap.update(billing_svc.daily_free_status(user))
    return snap


# --- POST /api/billing/estimate -------------------------------------------

@router.post("/api/billing/estimate", response_model=EstimateOut)
async def billing_estimate(body: EstimateIn):
    """Cost estimator. Frontend pre-flights a charge so the wallet UI can
    show "this run will cost N credits" before submission.

    Worst-case sizing -- assumes every track/song is a cache miss. The
    actual charge_credits call (in M3) settles to the real cost after the
    engine resolves cache hit vs miss per-song.
    """
    breakdown: dict = {}
    total = 0
    if body.word_count and body.word_count > 0:
        # Words don't directly cost; engine runs do. A 'song' (one calibrate
        # call) costs COST_SONG_MISS as the worst case. Frontend feeds word
        # count for forward compatibility (per-1k tiered pricing) -- we
        # round up to one song-run for any non-zero word count today.
        song_runs = 1
        cost = song_runs * billing_config.COST_SONG_MISS
        breakdown["song_runs"] = {"count": song_runs, "cost_each": billing_config.COST_SONG_MISS, "subtotal": cost}
        total += cost
    if body.track_count and body.track_count > 0:
        cost = body.track_count * billing_config.COST_ALBUM_TRACK_MISS
        breakdown["album_tracks"] = {"count": body.track_count, "cost_each": billing_config.COST_ALBUM_TRACK_MISS, "subtotal": cost}
        total += cost
    return EstimateOut(credits=total, breakdown=breakdown)


# --- GET /api/launch-status -----------------------------------------------

@router.get("/api/launch-status")
async def launch_status():
    """Public pre-launch gate state. The frontend fades the sign-up form +
    billing buttons when locked. Unauthenticated so any page can read it."""
    db = SessionLocal()
    try:
        locked = is_launch_locked(db)
        return {"locked": locked, "message": launch_locked_message(db) if locked else ""}
    finally:
        db.close()


# --- POST /api/billing-webhook --------------------------------------------

@router.post("/api/billing-webhook")
async def billing_webhook(request: Request, stripe_signature: str = Header(default="")):
    """Stripe -> RC billing event sink.

    Mirrors donate.py:stripe_webhook: verify signature, dispatch by event
    type, always return 200. Stripe retries 5xx with exponential backoff;
    a silent 200 on a noisy event type is the friendliest behaviour.

    Grants are idempotent via credit_ledger UNIQUE(reason, ref_id) -- the
    Stripe event.id is the ref_id, so a replayed event is a no-op.
    """
    payload = await request.body()
    if not stripe_signature:
        raise HTTPException(400, "Missing Stripe-Signature header")

    try:
        event = construct_billing_event(payload, stripe_signature)
    except RuntimeError as e:
        logger.error("Billing webhook secret not configured: %s", e)
        raise HTTPException(503, "Billing webhook not configured.")
    except stripe.error.SignatureVerificationError:
        logger.warning("Billing webhook signature verification failed")
        raise HTTPException(400, "Invalid signature")
    except Exception:
        logger.exception("Billing webhook parse failed")
        raise HTTPException(400, "Invalid payload")

    event_type = event.get("type", "")
    event_id = event.get("id", "")
    obj = event.get("data", {}).get("object", {})

    try:
        if event_type == "checkout.session.completed":
            _handle_checkout_completed(event_id, obj)
        elif event_type == "invoice.paid":
            _handle_invoice_paid(event_id, obj)
        elif event_type == "customer.subscription.updated":
            _handle_subscription_updated(obj)
        elif event_type == "customer.subscription.deleted":
            _handle_subscription_deleted(obj)
        else:
            logger.debug("billing-webhook: ignoring %s", event_type)
    except Exception:
        # Never raise out of the webhook -- Stripe will retry 5xx
        # indefinitely. We've already verified the signature; persistent
        # local failures are reconciled by hand from the Stripe dashboard.
        logger.exception("billing-webhook handler failed for %s id=%s", event_type, event_id)

    return {"received": True}


# --- Webhook handlers -----------------------------------------------------

def _invoice_subscription_id(invoice: dict) -> Optional[str]:
    """Pull the subscription id off an Invoice object across Stripe API
    versions. Classic versions expose invoice.subscription; 2025-03-31.basil+
    moves it under invoice.parent.subscription_details.subscription. Falls
    back to the first line item's subscription reference."""
    sid = invoice.get("subscription")
    if isinstance(sid, str) and sid:
        return sid
    parent = invoice.get("parent") or {}
    sd = parent.get("subscription_details") or {}
    if isinstance(sd.get("subscription"), str) and sd["subscription"]:
        return sd["subscription"]
    for ln in ((invoice.get("lines") or {}).get("data") or []):
        s = ln.get("subscription")
        if isinstance(s, str) and s:
            return s
    return None


def _resolve_user(
    *,
    user_id_str: Optional[str] = None,
    customer_id: Optional[str] = None,
    subscription_id: Optional[str] = None,
) -> Optional[User]:
    """Look up the local User row for a Stripe webhook event.

    Prefer client_reference_id (set on every Checkout we create); fall back
    to stripe_customer_id, then to stripe_subscription_id. The last two let
    recurring events (invoice.paid, subscription.updated/deleted) resolve the
    user even if the events arrive out of order relative to the Checkout that
    first stamped the customer id.
    """
    db = SessionLocal()
    try:
        if user_id_str:
            try:
                uid = int(user_id_str)
            except (TypeError, ValueError):
                uid = None
            if uid is not None:
                row = db.query(User).filter(User.id == uid).first()
                if row:
                    return row
        if customer_id:
            row = db.query(User).filter(User.stripe_customer_id == customer_id).first()
            if row:
                return row
        if subscription_id:
            row = db.query(User).filter(User.stripe_subscription_id == subscription_id).first()
            if row:
                return row
    finally:
        db.close()
    return None


def _stamp_customer(user_id: int, customer_id: Optional[str]) -> None:
    """Persist stripe_customer_id on the User row once Stripe tells us about it."""
    if not customer_id:
        return
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user_id).first()
        if u and u.stripe_customer_id != customer_id:
            u.stripe_customer_id = customer_id
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("stamp_customer failed for user %s", user_id)
    finally:
        db.close()


def _apply_subscription_state(user_id: int, sub: dict) -> None:
    """Mirror the relevant Stripe Subscription fields onto the User row."""
    sub_id = sub.get("id")
    status = sub.get("status")
    metadata = sub.get("metadata") or {}
    tier_key = metadata.get("tier")
    if not tier_key:
        # Fall back to deriving the tier from the subscribed price.
        items = (sub.get("items") or {}).get("data") or []
        price_id = (items[0].get("price") or {}).get("id") if items else None
        for t in billing_config.TIERS.values():
            if t.stripe_price_id and price_id == t.stripe_price_id:
                tier_key = t.key
                break
    if tier_key not in billing_config.TIERS:
        tier_key = "free"
    period_end_ts = sub.get("current_period_end")
    if not period_end_ts:
        # Stripe 2025-03-31.basil+ moved current_period_end off the root
        # Subscription object onto items.data[].current_period_end.
        items = (sub.get("items") or {}).get("data") or []
        if items:
            period_end_ts = items[0].get("current_period_end")

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user_id).first()
        if not u:
            return
        u.stripe_subscription_id = sub_id
        u.subscription_tier = tier_key
        u.subscription_status = status
        if period_end_ts:
            u.subscription_period_end = datetime.fromtimestamp(int(period_end_ts), timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("apply_subscription_state failed for user %s", user_id)
    finally:
        db.close()


def _handle_checkout_completed(event_id: str, obj: dict) -> None:
    """Dispatch on Checkout mode: subscription -> tier+allowance grant;
    payment -> pack purchase grant."""
    mode = obj.get("mode")
    metadata = obj.get("metadata") or {}
    user = _resolve_user(
        user_id_str=obj.get("client_reference_id") or metadata.get("user_id"),
        customer_id=obj.get("customer"),
    )
    if not user:
        logger.warning("checkout.session.completed: no user for session %s", obj.get("id"))
        return
    _stamp_customer(user.id, obj.get("customer"))

    if mode == "subscription":
        # Flip tier / customer / period_end from the authoritative
        # subscription. The first-month allowance is deliberately NOT granted
        # here -- invoice.paid is the single grant authority per period (it
        # fires for the initial invoice too), so the allowance is written to
        # the ledger exactly once and the ledger sum stays consistent with the
        # row balance.
        sub_id = obj.get("subscription")
        if sub_id:
            try:
                sub = fetch_subscription(sub_id)
                _apply_subscription_state(user.id, sub if isinstance(sub, dict) else sub.to_dict())
            except Exception:
                logger.exception("fetch_subscription failed for %s", sub_id)

    elif mode == "payment":
        # Credit pack one-time purchase. The pack key + credit count was
        # stamped on the session metadata at creation; trust it.
        try:
            credits = int(metadata.get("credits") or 0)
        except (TypeError, ValueError):
            credits = 0
        pack_key = metadata.get("pack_key") or "unknown_pack"
        if credits <= 0:
            logger.warning("checkout.session.completed pack with no credits metadata: %s", obj.get("id"))
            return
        billing_svc.grant_credits(
            user.id,
            credits,
            bucket="purchased",
            reason="pack_purchase",
            ref_type="stripe_event",
            ref_id=event_id,
            context={"pack_key": pack_key, "session_id": obj.get("id")},
        )
        amount_total = obj.get("amount_total")
        posthog_analytics.capture(
            user.clerk_user_id,
            "credit_pack_purchased",
            {
                "pack_key": pack_key,
                "credits": credits,
                "revenue": (amount_total / 100.0) if isinstance(amount_total, int) else None,
                "currency": (obj.get("currency") or "usd").upper(),
            },
        )


def _handle_invoice_paid(event_id: str, obj: dict) -> None:
    """Sole monthly-allowance grant authority. Fires for the first invoice AND
    every renewal. Derives the tier from the subscription itself (not the
    possibly-stale user row) so an upgrade is honoured even when invoice.paid
    is processed before customer.subscription.updated. Idempotent via
    (monthly_grant, invoice_id) so a replayed invoice.paid is a no-op."""
    sub_id = _invoice_subscription_id(obj)
    user = _resolve_user(
        user_id_str=(obj.get("metadata") or {}).get("user_id"),
        customer_id=obj.get("customer"),
        subscription_id=sub_id,
    )
    if not user:
        logger.warning("invoice.paid: no user for customer %s", obj.get("customer"))
        return
    _stamp_customer(user.id, obj.get("customer"))

    # Sync tier/status/period_end from the authoritative subscription and
    # derive the tier to grant from it -- not from the user row, which may not
    # yet reflect an in-flight upgrade.
    tier_key = None
    if sub_id:
        try:
            sub = fetch_subscription(sub_id)
            sub_dict = sub if isinstance(sub, dict) else sub.to_dict()
            _apply_subscription_state(user.id, sub_dict)
            tier_key = (sub_dict.get("metadata") or {}).get("tier")
            if not tier_key:
                items = (sub_dict.get("items") or {}).get("data") or []
                price_id = (items[0].get("price") or {}).get("id") if items else None
                for t in billing_config.TIERS.values():
                    if t.stripe_price_id and price_id == t.stripe_price_id:
                        tier_key = t.key
                        break
        except Exception:
            logger.exception("invoice.paid: fetch_subscription failed for %s", sub_id)

    if not tier_key:
        # Fall back to the (now possibly-synced) persisted row tier.
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.id == user.id).first()
            tier_key = (u.subscription_tier if u else None) or "free"
        finally:
            db.close()

    tier = billing_config.TIERS.get(tier_key, billing_config.TIER_FREE)
    if tier.monthly_allowance <= 0:
        return
    # Invoice id is stable across retries, so a replayed invoice.paid for the
    # same period collides on (monthly_grant, invoice_id, allowance) and no-ops.
    invoice_id = obj.get("id") or event_id
    billing_svc.grant_credits(
        user.id,
        tier.monthly_allowance,
        bucket="allowance",
        reason="monthly_grant",
        ref_type="stripe_invoice",
        ref_id=invoice_id,
        context={"tier": tier.key},
        reset_allowance=True,
    )

    # Server-side revenue event (invoice.paid is the single authority, so this
    # is the one place subscription revenue is counted). billing_reason splits
    # the first charge from renewals so they don't double-count in funnels.
    billing_reason = obj.get("billing_reason")
    sub_event = (
        "subscription_started" if billing_reason == "subscription_create"
        else "subscription_renewed" if billing_reason == "subscription_cycle"
        else "subscription_payment"
    )
    amount_paid = obj.get("amount_paid")
    posthog_analytics.capture(
        user.clerk_user_id,
        sub_event,
        {
            "tier": tier.key,
            "billing_reason": billing_reason,
            "revenue": (amount_paid / 100.0) if isinstance(amount_paid, int) else None,
            "currency": (obj.get("currency") or "usd").upper(),
        },
    )


def _handle_subscription_updated(obj: dict) -> None:
    user = _resolve_user(
        user_id_str=(obj.get("metadata") or {}).get("user_id"),
        customer_id=obj.get("customer"),
        subscription_id=obj.get("id"),
    )
    if not user:
        return
    _apply_subscription_state(user.id, obj)


def _handle_subscription_deleted(obj: dict) -> None:
    """Subscription canceled or ended. Drop to free + zero allowance; the
    purchased bucket is permanent and survives the downgrade. The forfeited
    allowance is recorded as a negative ledger row so the ledger sum keeps
    matching the row balance."""
    sub_id = obj.get("id")
    user = _resolve_user(
        user_id_str=(obj.get("metadata") or {}).get("user_id"),
        customer_id=obj.get("customer"),
        subscription_id=sub_id,
    )
    if not user:
        return
    db = SessionLocal()
    try:
        u = (
            db.query(User)
            .filter(User.id == user.id)
            .with_for_update()
            .first()
        )
        if not u:
            return
        prior = u.allowance_credits or 0
        prior_tier = u.subscription_tier
        if prior > 0:
            db.add(CreditLedger(
                user_id=u.id, delta=-prior, bucket="allowance",
                reason="allowance_expire", ref_type="stripe_event",
                ref_id=(f"{sub_id}:cancel:expire" if sub_id else None),
                context_json=json.dumps({"expired": prior, "kind": "cancel"}),
            ))
        u.subscription_tier = "free"
        u.subscription_status = "canceled"
        u.subscription_period_end = None
        u.stripe_subscription_id = None
        u.allowance_credits = 0
        db.commit()
        posthog_analytics.capture(
            user.clerk_user_id,
            "subscription_canceled",
            {"prior_tier": prior_tier},
        )
    except Exception:
        db.rollback()
        logger.exception("subscription.deleted handler failed for user %s", user.id)
    finally:
        db.close()
