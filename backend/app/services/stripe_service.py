"""Stripe wrapper for Lyrical Charger donations.

Mirrors chadlewine/src/lib/stripe.ts. Same Stripe account, separate
webhook endpoint with its own signing secret.

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
