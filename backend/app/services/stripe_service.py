"""Stripe wrapper for Lyrical Charger donations.

Runs on the dedicated Rising Compass Stripe account (migrated off the shared
chadlewine account at launch, 2026-05-29). Donations, subscriptions, and
credit packs all use the one settings.stripe_secret_key; the donation webhook
has its own signing secret (settings.stripe_webhook_secret), distinct from the
billing + identity webhook secrets so a leak of one can't forge the others.

Donations are mode='payment' (one-time gift), submit_type='donate' so
Stripe Checkout shows a "Donate" button instead of "Pay". Line items
carry a source tag in the product name so receipts read
"Donation: Lyrical Charger" rather than a bare "Donation".
"""

import logging
from typing import Optional

import stripe

from app.config import settings

logger = logging.getLogger(__name__)


def _client() -> stripe.StripeClient:
    """Return a per-call Stripe client. The library is thread-safe; we
    make a new client each call so a key rotation in .env applies after
    the next process restart without needing module reload tricks."""
    return stripe.StripeClient(api_key=settings.stripe_secret_key)


def to_cents(dollars: float) -> int:
    return int(round(float(dollars) * 100))


def create_donation_session(
    *,
    amount_dollars: float,
    success_url: str,
    cancel_url: str,
    source: str,
) -> stripe.checkout.Session:
    """Create a one-shot Stripe Checkout session for a donation.

    Returns the full Session object. The caller persists session.id +
    amount_cents in rc_donations and redirects the user to session.url.
    """
    if not settings.stripe_secret_key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")

    cents = to_cents(amount_dollars)
    if cents < 100:
        raise ValueError("Minimum donation is $1.00")

    product_name = f"Donation: {source}" if source else "Donation"

    return _client().checkout.sessions.create(
        params={
            "mode": "payment",
            "submit_type": "donate",
            "line_items": [
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {"name": product_name},
                        "unit_amount": cents,
                    },
                    "quantity": 1,
                }
            ],
            "metadata": {"source": source or ""},
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
    )


def construct_event(payload: bytes, signature: str) -> stripe.Event:
    """Verify a webhook signature and return the parsed Event.

    Raises stripe.error.SignatureVerificationError when the signature
    is invalid — the router maps that to a 400.
    """
    if not settings.stripe_webhook_secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not configured")
    return stripe.Webhook.construct_event(
        payload=payload,
        sig_header=signature,
        secret=settings.stripe_webhook_secret,
    )


# --- Billing (subscriptions + credit packs, M2) ----------------------------

def construct_billing_event(payload: bytes, signature: str) -> stripe.Event:
    """Verify a webhook payload against the billing-specific signing secret.

    The billing webhook is a distinct endpoint with its own secret so a
    leaked donation/identity webhook key can't forge subscription or
    pack-purchase events (and vice versa).
    """
    if not settings.stripe_billing_webhook_secret:
        raise RuntimeError("STRIPE_BILLING_WEBHOOK_SECRET is not configured")
    return stripe.Webhook.construct_event(
        payload=payload,
        sig_header=signature,
        secret=settings.stripe_billing_webhook_secret,
    )


def create_subscription_session(
    *,
    price_id: str,
    user_id: int,
    success_url: str,
    cancel_url: str,
    customer_id: Optional[str] = None,
    customer_email: Optional[str] = None,
    tier_key: str,
) -> stripe.checkout.Session:
    """Create a Stripe Checkout session for a tier subscription.

    `client_reference_id` carries the local user id so the webhook can
    map the customer back to a User row before stripe_customer_id is
    persisted. `metadata.tier` lets checkout.session.completed flip the
    user's subscription_tier without round-tripping Stripe again.
    """
    if not settings.stripe_secret_key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")
    if not price_id:
        raise ValueError("price_id is required")

    params: dict = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "client_reference_id": str(user_id),
        "metadata": {"user_id": str(user_id), "tier": tier_key, "kind": "subscription"},
        "subscription_data": {
            "metadata": {"user_id": str(user_id), "tier": tier_key},
        },
        "success_url": success_url,
        "cancel_url": cancel_url,
        "allow_promotion_codes": True,
    }
    if customer_id:
        params["customer"] = customer_id
    elif customer_email:
        params["customer_email"] = customer_email

    return _client().checkout.sessions.create(params=params)


def create_credit_pack_session(
    *,
    price_id: str,
    pack_key: str,
    credits: int,
    user_id: int,
    success_url: str,
    cancel_url: str,
    customer_id: Optional[str] = None,
    customer_email: Optional[str] = None,
) -> stripe.checkout.Session:
    """Create a Stripe Checkout session for a one-time credit pack.

    mode='payment' (one-shot). metadata.kind='pack' + metadata.credits lets
    the webhook size the grant without re-querying the price.
    """
    if not settings.stripe_secret_key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")
    if not price_id:
        raise ValueError("price_id is required")

    params: dict = {
        "mode": "payment",
        "line_items": [{"price": price_id, "quantity": 1}],
        "client_reference_id": str(user_id),
        "metadata": {
            "user_id": str(user_id),
            "kind": "pack",
            "pack_key": pack_key,
            "credits": str(credits),
        },
        "success_url": success_url,
        "cancel_url": cancel_url,
        "allow_promotion_codes": True,
    }
    if customer_id:
        params["customer"] = customer_id
    elif customer_email:
        params["customer_email"] = customer_email

    return _client().checkout.sessions.create(params=params)


def fetch_subscription(subscription_id: str) -> stripe.Subscription:
    """Read a subscription by id. Used by webhook handlers to derive
    tier + period_end + status without trusting the (possibly partial)
    event payload."""
    return _client().subscriptions.retrieve(subscription_id)
