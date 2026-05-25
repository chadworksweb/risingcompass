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


def retrieve_verified_name(session_id: str) -> "tuple[str, str] | None":
    """Pull the verified legal name from a completed VerificationSession.

    Stripe exposes the matched document name on `verified_outputs`
    (first_name / last_name) once the session is `verified`. Returns a
    (first, last) tuple, or None if either the outputs are unavailable or
    no name was captured. Best-effort: the caller treats None as "no name
    to store" and falls back to handle display."""
    if not settings.stripe_secret_key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")
    # verified_outputs holds the matched PII and is redacted unless
    # explicitly expanded on retrieve -- without this it comes back None.
    vs = _client().identity.verification_sessions.retrieve(
        session_id, params={"expand": ["verified_outputs"]}
    )
    outputs = getattr(vs, "verified_outputs", None)
    if not outputs:
        return None
    first = (getattr(outputs, "first_name", None) or "").strip()
    last = (getattr(outputs, "last_name", None) or "").strip()
    if not first and not last:
        return None
    return first, last


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
