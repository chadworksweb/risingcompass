"""Stripe wrapper for the RC shop's cart checkout.

Ports chadlewine's lib/stripe.ts cart pieces to the RC Stripe account (the same
dedicated account billing + donations use). The shop always has physical lines,
so checkout runs in EMBEDDED mode: only embedded checkout supports the
address-driven dynamic shipping callback (onShippingDetailsChange -> the
calculate-shipping route recomputes the Printify quote and writes it back onto
the session with update_session_shipping).

The cart webhook is a distinct endpoint with its own signing secret
(stripe_shop_webhook_secret) so a leak on the donation/billing/identity streams
can't forge shop-order events (and vice versa).
"""

from __future__ import annotations

from typing import Optional

import stripe

from app.config import settings

# Countries the shop ships to. Matches chadlewine's allowlist.
ALLOWED_COUNTRIES = ["US", "CA", "GB", "AU", "NZ", "IE"]


def _client() -> stripe.StripeClient:
    return stripe.StripeClient(api_key=settings.stripe_secret_key)


def to_cents(dollars: float) -> int:
    return int(round(float(dollars) * 100))


def create_cart_checkout_session(
    *,
    line_items: list[dict],
    cart_items_metadata: str,
    return_url: str,
    extra_metadata: Optional[dict] = None,
    customer_email: Optional[str] = None,
) -> stripe.checkout.Session:
    """Create an EMBEDDED Stripe Checkout session for a physical cart.

    line_items: [{"title","description"?,"price" (dollars),"image_url"?,
                  "quantity"}]
    cart_items_metadata: compact JSON the webhook + shipping calc read back to
                         resolve Printify line items (product_id/variant_id/qty).
    A $0 placeholder shipping rate is attached and locked to server_only; the
    real rate is written at the onShippingDetailsChange callback.
    """
    if not settings.stripe_secret_key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")

    stripe_line_items = []
    for li in line_items:
        product_data: dict = {"name": li["title"]}
        if li.get("description"):
            product_data["description"] = li["description"]
        if li.get("image_url"):
            product_data["images"] = [li["image_url"]]
        stripe_line_items.append(
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": product_data,
                    "unit_amount": to_cents(li["price"]),
                },
                "quantity": int(li.get("quantity", 1)),
            }
        )

    metadata = {"type": "shop_cart", "cart_items": cart_items_metadata}
    if extra_metadata:
        metadata.update(extra_metadata)

    params: dict = {
        "mode": "payment",
        "ui_mode": "embedded",
        "return_url": return_url,
        "line_items": stripe_line_items,
        "metadata": metadata,
        "customer_creation": "always",
        "shipping_address_collection": {"allowed_countries": ALLOWED_COUNTRIES},
        "phone_number_collection": {"enabled": True},
        "permissions": {"update_shipping_details": "server_only"},
        "shipping_options": [
            {
                "shipping_rate_data": {
                    "type": "fixed_amount",
                    "fixed_amount": {"amount": 0, "currency": "usd"},
                    "display_name": "Calculated from your address",
                }
            }
        ],
    }
    if customer_email:
        params["customer_email"] = customer_email

    return _client().checkout.sessions.create(params=params)


def retrieve_session(session_id: str) -> stripe.checkout.Session:
    return _client().checkout.sessions.retrieve(session_id)


def update_session_shipping(
    *,
    session_id: str,
    shipping_details: dict,
    amount_cents: int,
    display_name: str,
) -> stripe.checkout.Session:
    """Server-only shipping update for embedded checkout. Writes the confirmed
    address back onto the session and replaces the placeholder rate with the
    computed one."""
    return _client().checkout.sessions.update(
        session_id,
        params={
            "collected_information": {"shipping_details": shipping_details},
            "shipping_options": [
                {
                    "shipping_rate_data": {
                        "type": "fixed_amount",
                        "fixed_amount": {"amount": amount_cents, "currency": "usd"},
                        "display_name": display_name,
                    }
                }
            ],
        },
    )


def construct_shop_event(payload: bytes, signature: str) -> stripe.Event:
    """Verify the shop cart webhook signature against its dedicated secret."""
    if not settings.stripe_shop_webhook_secret:
        raise RuntimeError("STRIPE_SHOP_WEBHOOK_SECRET is not configured")
    return stripe.Webhook.construct_event(
        payload=payload,
        sig_header=signature,
        secret=settings.stripe_shop_webhook_secret,
    )
