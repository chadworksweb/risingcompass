"""Stripe Identity wrapper for Tier 2 verification.

Mirrors the stripe_service.py pattern: thin functional wrapper around the
Stripe SDK with secrets pulled from app.config. Same Stripe account as
donations, distinct webhook endpoint with its own signing secret so the
two event streams can be rotated independently.

Stripe Identity is $1.50 per verification at time of writing. Gate
verification behind a Tier 1 user who explicitly opts in (the Motion
Desk button on /account/) -- never auto-charge.

Reference:
  https://stripe.com/docs/identity/verification-sessions
  https://stripe.com/docs/identity/handle-verification-outcomes
"""

import logging

import stripe

from app.config import settings

logger = logging.getLogger(__name__)


def _client() -> stripe.StripeClient:
    return stripe.StripeClient(api_key=settings.stripe_secret_key)


def create_verification_session(
    *,
    rc_user_id: int,
    return_url: str,
) -> stripe.identity.VerificationSession:
    """Open a new Stripe Identity verification session for the given local
    user id. Returns the full Session object -- caller persists session.id
    in account_verifications and redirects the browser to session.url
    (hosted flow) or hands session.client_secret to Stripe.js for embedded.

    metadata.rc_user_id is the link back to our users.id when the webhook
    fires; trust this over any client-side hint."""
    if not settings.stripe_secret_key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")
    return _client().identity.verification_sessions.create(
        params={
            "type": "document",
            "metadata": {"rc_user_id": str(rc_user_id)},
            "return_url": return_url,
            "options": {
                "document": {
                    "require_id_number": False,
                    "require_live_capture": True,
                    "require_matching_selfie": True,
                },
            },
        }
    )


def construct_event(payload: bytes, signature: str) -> stripe.Event:
    """Verify the Stripe Identity webhook signature. Distinct signing
    secret from the donation webhook so a leak is scoped."""
    if not settings.stripe_identity_webhook_secret:
        raise RuntimeError("STRIPE_IDENTITY_WEBHOOK_SECRET is not configured")
    return stripe.Webhook.construct_event(
        payload=payload,
        sig_header=signature,
        secret=settings.stripe_identity_webhook_secret,
    )
