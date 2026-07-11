"""Shop (Printify merch storefront at /shop/).

The RC port of chadlewine.com's core ecom buy flow:

  GET  /api/shop/config              -> publishable key + shipping threshold for the frontend
  GET  /api/shop/products            -> active products for the grid
  GET  /api/shop/products/{slug}     -> one product (variant picker)
  POST /api/shop/cart-checkout       -> create an EMBEDDED Stripe session, return client_secret
  POST /api/shop/calculate-shipping  -> Stripe onShippingDetailsChange -> live Printify quote
  POST /api/shop/webhook             -> Stripe cart webhook: create order + push to Printify
  POST /api/shop/printify-webhook    -> Printify order-status updates

Products live in shop_products (synced from Printify). Payment is Stripe embedded
Checkout on the dedicated RC Stripe account; the address-driven shipping callback
quotes Printify live. On checkout.session.completed the order is recorded and an
order is created in Printify + sent to production. Anonymous checkout is allowed
(keyed by the email Stripe collects); a signed-in Clerk user is attached via
client_reference_id when present.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.auth import (
    optional_clerk_user,
    require_admin_session,
    verify_api_key,
    verify_api_or_service_key,
)
from app.config import settings
from app.database import SessionLocal
from app.models import ShopOrder, ShopProduct, ShopSubscriber, User
from app.services import alerts, feature_flags, printify_service, shop_email, shop_stripe

logger = logging.getLogger(__name__)

router = APIRouter(tags=["shop"])
admin_router = APIRouter(tags=["shop-admin"])

_MAX_CART_LINES = 10
_MAX_QTY_PER_LINE = 10

# Redirect-origin allowlist (mirrors donate.py) -- Stripe uses return_url
# verbatim, so only accept RC origins.
_ALLOWED_RETURN_ORIGINS = {
    "https://risingcompass.net",
    "https://www.risingcompass.net",
    "http://localhost:3000",
    "http://localhost:3005",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3005",
}


def _origin_of(url: str) -> Optional[str]:
    try:
        u = urlparse(url)
        if not u.scheme or not u.netloc:
            return None
        return f"{u.scheme}://{u.netloc}"
    except Exception:
        return None


def _request_origin(request: Request) -> str:
    origin = request.headers.get("origin")
    if origin and origin in _ALLOWED_RETURN_ORIGINS:
        return origin
    ref = request.headers.get("referer")
    ro = _origin_of(ref) if ref else None
    if ro and ro in _ALLOWED_RETURN_ORIGINS:
        return ro
    return "https://risingcompass.net"


def _variant_label(v: dict) -> str:
    """Human label for a variant, e.g. "Black / L" or "L" or the raw title."""
    size = v.get("size")
    color = v.get("color")
    if color and size:
        return f"{color} / {size}"
    if size:
        return size
    if color:
        return color
    return v.get("title") or ""


def _colors_from_variants(variants: list[dict]) -> list[dict]:
    """Distinct colors in first-seen order, each with its own front mockup.
    Each color is an individual SKU line in Printify; this surfaces them for the
    swatch UI (grid card badge + product-page color picker + hero swap)."""
    seen: set[str] = set()
    out: list[dict] = []
    for v in variants:
        c = v.get("color")
        if c and c not in seen:
            seen.add(c)
            out.append({"name": c, "image": v.get("image") or None})
    return out


def _product_out(p: ShopProduct, *, detail: bool) -> dict:
    variants = json.loads(p.variants) if p.variants else []
    out = {
        "slug": p.slug,
        "title": p.title,
        "image_url": p.image_url,
        "price": p.price,
        "colors": _colors_from_variants(variants),
    }
    if detail:
        out["description"] = p.description
        out["image_urls"] = json.loads(p.image_urls) if p.image_urls else (
            [p.image_url] if p.image_url else []
        )
        out["variants"] = variants
    return out


def _resolve_variant(db, printify_product_id: str, variant_id: int):
    """Return (ShopProduct, variant_dict) for an active product + enabled
    variant, or (None, None)."""
    product = (
        db.query(ShopProduct)
        .filter(ShopProduct.printify_product_id == printify_product_id)
        .filter(ShopProduct.status == "active")
        .one_or_none()
    )
    if not product:
        return None, None
    variants = json.loads(product.variants) if product.variants else []
    variant = next((v for v in variants if v.get("id") == variant_id), None)
    return (product, variant) if variant else (None, None)


# ---------------------------------------------------------------------------
# Public read endpoints
# ---------------------------------------------------------------------------

@router.get("/api/shop/config", dependencies=[Depends(verify_api_key)])
async def shop_config():
    """Public config for the shop frontend: whether the shop is open (dark-launch
    gate), the coming-soon copy, and the Stripe publishable key + shipping info
    it needs to mount embedded Checkout when open."""
    db = SessionLocal()
    try:
        available = feature_flags.is_shop_enabled(db)
        coming_soon = feature_flags.shop_coming_soon_message(db)
    finally:
        db.close()
    return {
        "available": available,
        "coming_soon_message": coming_soon,
        "stripe_publishable_key": settings.stripe_publishable_key,
        "currency": "usd",
        "free_shipping_threshold_cents": settings.shop_free_us_threshold_cents,
        "allowed_countries": shop_stripe.ALLOWED_COUNTRIES,
        "configured": bool(
            settings.stripe_publishable_key and printify_service.is_configured()
        ),
    }


@router.get("/api/shop/products", dependencies=[Depends(verify_api_key)])
async def shop_products():
    # Catalog is browsable even before launch (preview mode). Only the purchase
    # path (cart-checkout) is gated by shop.enabled.
    db = SessionLocal()
    try:
        rows = (
            db.query(ShopProduct)
            .filter(ShopProduct.status == "active")
            .order_by(ShopProduct.display_order.asc(), ShopProduct.created_at.asc())
            .all()
        )
        return {"products": [_product_out(p, detail=False) for p in rows]}
    finally:
        db.close()


@router.get("/api/shop/products/{slug}", dependencies=[Depends(verify_api_key)])
async def shop_product_detail(slug: str):
    db = SessionLocal()
    try:
        p = (
            db.query(ShopProduct)
            .filter(ShopProduct.slug == slug)
            .filter(ShopProduct.status == "active")
            .one_or_none()
        )
        if not p:
            raise HTTPException(404, "Product not found")
        return _product_out(p, detail=True)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Coming-soon / notify-me capture (dark launch). Always open, even while the
# shop is dark -- that's the whole point.
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ShopSubscribeIn(BaseModel):
    email: str = Field(..., max_length=254)
    hp_website: str = Field("", max_length=200)  # honeypot
    turnstile_token: str = Field("", max_length=4000)


@router.post("/api/shop/subscribe", dependencies=[Depends(verify_api_key)])
async def shop_subscribe(body: ShopSubscribeIn, request: Request):
    """Capture an email to notify when the shop opens. Honeypot + Turnstile
    (when configured) via the shared analyzer bot check."""
    from app.routers import analyzer  # lazy import to avoid import cycle weight
    await analyzer._check_bot_protection(body.hp_website, body.turnstile_token, request)

    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(422, "Please enter a valid email address.")

    db = SessionLocal()
    try:
        existing = (
            db.query(ShopSubscriber).filter(ShopSubscriber.email == email).first()
        )
        if existing:
            return {"status": "already_subscribed",
                    "message": "You're already on the list. We'll email you when the shop opens."}
        db.add(ShopSubscriber(email=email))
        db.commit()
        return {"status": "subscribed",
                "message": "Thanks. We'll email you when the shop opens."}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Cart checkout
# ---------------------------------------------------------------------------

class CartLineIn(BaseModel):
    slug: str = Field(..., max_length=200)
    variant_id: int
    quantity: int = Field(1, ge=1, le=_MAX_QTY_PER_LINE)


class CartCheckoutIn(BaseModel):
    items: list[CartLineIn]
    return_url: str = Field(..., max_length=500)


@router.post("/api/shop/cart-checkout",
             dependencies=[Depends(verify_api_or_service_key)])
async def cart_checkout(
    body: CartCheckoutIn,
    request: Request,
    user: Optional[User] = Depends(optional_clerk_user),
):
    if not body.items:
        raise HTTPException(400, "Your cart is empty.")
    if len(body.items) > _MAX_CART_LINES:
        raise HTTPException(400, "Cart too large.")
    if not settings.stripe_secret_key or not settings.stripe_publishable_key:
        raise HTTPException(503, "The shop is not configured for checkout yet.")

    origin = _request_origin(request)
    # return_url must be a same-origin RC URL (Stripe uses it verbatim).
    if _origin_of(body.return_url) not in _ALLOWED_RETURN_ORIGINS:
        raise HTTPException(400, "return_url origin not allowed")

    db = SessionLocal()
    try:
        if not feature_flags.is_shop_enabled(db):
            raise HTTPException(503, "The shop is not open yet.")
        line_items: list[dict] = []
        cart_meta: list[dict] = []
        seen: set[tuple[str, int]] = set()
        for raw in body.items:
            product = (
                db.query(ShopProduct)
                .filter(ShopProduct.slug == raw.slug)
                .filter(ShopProduct.status == "active")
                .one_or_none()
            )
            if not product:
                raise HTTPException(404, f'"{raw.slug}" is not available.')
            variants = json.loads(product.variants) if product.variants else []
            variant = next((v for v in variants if v.get("id") == raw.variant_id), None)
            if not variant:
                raise HTTPException(400, f'Please choose an available option for "{product.title}".')

            key = (product.printify_product_id, raw.variant_id)
            if key in seen:
                continue  # dedupe identical lines
            seen.add(key)

            price_cents = int(variant.get("price_cents") or 0)
            if price_cents <= 0:
                raise HTTPException(400, f'"{product.title}" has no price set.')

            label = _variant_label(variant)
            line_items.append({
                "title": product.title,
                "description": label or None,
                "price": price_cents / 100.0,
                "image_url": product.image_url,
                "quantity": raw.quantity,
            })
            cart_meta.append({
                "p": product.printify_product_id,
                "v": raw.variant_id,
                "q": raw.quantity,
            })
    finally:
        db.close()

    if not line_items:
        raise HTTPException(400, "Your cart is empty.")

    cart_json = json.dumps(cart_meta, separators=(",", ":"))
    if len(cart_json) > 500:
        raise HTTPException(400, "Cart too large to checkout -- remove a few items.")

    extra_meta: dict = {}
    if user is not None:
        extra_meta["rc_user_id"] = str(user.id)

    try:
        session = await run_in_threadpool(
            shop_stripe.create_cart_checkout_session,
            line_items=line_items,
            cart_items_metadata=cart_json,
            return_url=body.return_url,
            extra_metadata=extra_meta or None,
        )
    except stripe.error.StripeError as e:
        logger.exception("shop cart checkout: Stripe session create failed")
        raise HTTPException(502, f"Payment processor error: {e.user_message or 'try again'}")
    except RuntimeError as e:
        logger.error("shop cart checkout: %s", e)
        raise HTTPException(503, "The shop is not configured for checkout yet.")

    if not session.client_secret:
        raise HTTPException(500, "No checkout secret returned.")
    return {"client_secret": session.client_secret}


# ---------------------------------------------------------------------------
# Live shipping quote (Stripe embedded onShippingDetailsChange)
# ---------------------------------------------------------------------------

def _zone_is_us(country: Optional[str]) -> bool:
    return (country or "US").upper() == "US"


@router.post("/api/shop/calculate-shipping",
             dependencies=[Depends(verify_api_key)])
async def calculate_shipping(request: Request):
    body = await request.json()
    session_id = (body or {}).get("checkout_session_id")
    details = (body or {}).get("shipping_details") or {}
    address = details.get("address") or {}
    if not session_id or not address:
        return {"type": "reject", "errorMessage": "Missing shipping details."}

    try:
        session = await run_in_threadpool(shop_stripe.retrieve_session, session_id)
    except Exception:
        return {"type": "reject", "errorMessage": "Checkout session not found."}

    meta = session.metadata or {}
    if meta.get("type") != "shop_cart":
        return {"type": "reject", "errorMessage": "Invalid checkout session."}

    try:
        cart = json.loads(meta.get("cart_items") or "[]")
    except Exception:
        return {"type": "reject", "errorMessage": "Could not read your cart."}

    # Build Printify line items from the cart (all shop products are Printify).
    printify_line_items = [
        {"product_id": c["p"], "variant_id": c["v"], "quantity": c.get("q", 1)}
        for c in cart
        if c.get("p") and c.get("v")
    ]

    subtotal_cents = session.amount_subtotal or 0
    free_eligible = (
        _zone_is_us(address.get("country"))
        and subtotal_cents >= settings.shop_free_us_threshold_cents
    )

    amount_cents = 0
    if not free_eligible and printify_line_items:
        name = details.get("name") or "Customer"
        parts = name.split()
        address_to = {
            "first_name": parts[0] if parts else "Customer",
            "last_name": " ".join(parts[1:]) or "-",
            "email": "",
            "country": address.get("country") or "US",
            "region": address.get("state") or "",
            "address1": address.get("line1") or "",
            "address2": address.get("line2") or None,
            "city": address.get("city") or "",
            "zip": address.get("postal_code") or "",
        }
        try:
            quote = await run_in_threadpool(
                printify_service.get_order_shipping_cost,
                line_items=printify_line_items,
                address_to=address_to,
            )
            amount_cents = int(quote.get("standard") or 0)
        except printify_service.PrintifyError:
            # Never silently ship for free on an error -- ask the buyer to retry.
            logger.exception("shop shipping: Printify quote failed")
            return {
                "type": "reject",
                "errorMessage": "We couldn't calculate shipping for that address. Please try again in a moment.",
            }

    display_name = "Free shipping" if amount_cents == 0 else "Shipping"
    shipping_details = {
        "name": details.get("name") or "Customer",
        "address": {
            "line1": address.get("line1") or "",
            "line2": address.get("line2") or None,
            "city": address.get("city") or "",
            "state": address.get("state") or None,
            "postal_code": address.get("postal_code") or "",
            "country": address.get("country") or "US",
        },
    }
    try:
        await run_in_threadpool(
            shop_stripe.update_session_shipping,
            session_id=session_id,
            shipping_details=shipping_details,
            amount_cents=amount_cents,
            display_name=display_name,
        )
    except Exception:
        logger.exception("shop shipping: session update failed")
        return {"type": "reject", "errorMessage": "Could not apply shipping. Please try again."}

    return {"type": "accept"}


# ---------------------------------------------------------------------------
# Stripe cart webhook -> create order + push to Printify
# ---------------------------------------------------------------------------

def _new_order_number() -> str:
    return f"RC{datetime.utcnow().strftime('%y%m%d')}-{secrets.token_hex(3).upper()}"


def _push_order_to_printify(order_id: int) -> None:
    """Create the Printify order for a paid shop order + send to production.
    Runs its own short-lived session. Fail-soft: on error, stamp
    printify_error and leave status='paid' for a manual retry."""
    db = SessionLocal()
    try:
        order = db.query(ShopOrder).filter(ShopOrder.id == order_id).one_or_none()
        if not order or order.printify_order_id:
            return
        if not order.ship_line1 or not order.ship_city or not order.ship_country:
            order.printify_error = "missing shipping address"
            db.commit()
            return
        lines = json.loads(order.line_items) if order.line_items else []
        printify_lines = [
            {"product_id": l["printify_product_id"], "variant_id": l["variant_id"],
             "quantity": l.get("quantity", 1)}
            for l in lines
        ]
        parts = (order.buyer_name or "").split()
        payload = {
            "external_id": order.order_number,
            "label": order.order_number,
            "line_items": printify_lines,
            "shipping_method": 1,
            "send_shipping_notification": False,
            "address_to": {
                "first_name": parts[0] if parts else "Customer",
                "last_name": " ".join(parts[1:]) or "-",
                "email": order.buyer_email or "",
                "country": order.ship_country,
                "region": order.ship_state or "",
                "address1": order.ship_line1,
                "address2": order.ship_line2 or None,
                "city": order.ship_city,
                "zip": order.ship_zip or "",
            },
        }
        resp = printify_service.create_order(payload)
        printify_id = resp.get("id")
        order.printify_order_id = printify_id
        order.pushed_to_printify_at = datetime.utcnow()
        order.status = "in_production"
        order.printify_error = None
        db.commit()
        try:
            printify_service.send_order_to_production(printify_id)
        except printify_service.PrintifyError as e:
            logger.warning("shop: send_to_production failed for %s: %s", printify_id, e)
    except printify_service.PrintifyError as e:
        logger.error("shop: Printify create_order failed for order %s: %s", order_id, e)
        try:
            order = db.query(ShopOrder).filter(ShopOrder.id == order_id).one_or_none()
            if order:
                order.printify_error = str(e)[:300]
                db.commit()
        except Exception:
            db.rollback()
    except Exception:
        logger.exception("shop: unexpected error pushing order %s to Printify", order_id)
        db.rollback()
    finally:
        db.close()


@router.post("/api/shop/webhook")
async def shop_webhook(request: Request, stripe_signature: str = Header(default="")):
    payload = await request.body()
    if not stripe_signature:
        raise HTTPException(400, "Missing Stripe-Signature header")
    try:
        event = shop_stripe.construct_shop_event(payload, stripe_signature)
    except RuntimeError as e:
        logger.error("shop webhook not configured: %s", e)
        raise HTTPException(503, "Webhook not configured.")
    except stripe.error.SignatureVerificationError:
        logger.warning("shop webhook signature verification failed")
        raise HTTPException(400, "Invalid signature")
    except Exception:
        logger.exception("shop webhook parse failed")
        raise HTTPException(400, "Invalid payload")

    if event.get("type") != "checkout.session.completed":
        return {"received": True}

    obj = event.get("data", {}).get("object", {}) or {}
    if (obj.get("metadata") or {}).get("type") != "shop_cart":
        return {"received": True}

    session_id = obj.get("id")
    order_id_to_push: Optional[int] = None
    db = SessionLocal()
    try:
        # Idempotency: Stripe re-sends on 5xx.
        existing = (
            db.query(ShopOrder)
            .filter(ShopOrder.stripe_session_id == session_id)
            .one_or_none()
        )
        if existing:
            return {"received": True, "duplicate": True}

        meta = obj.get("metadata") or {}
        try:
            cart = json.loads(meta.get("cart_items") or "[]")
        except Exception:
            cart = []

        # Snapshot line items from the current product catalog.
        line_snapshot: list[dict] = []
        for c in cart:
            product, variant = _resolve_variant(db, c.get("p"), c.get("v"))
            qty = c.get("q", 1)
            if product and variant:
                line_snapshot.append({
                    "printify_product_id": product.printify_product_id,
                    "variant_id": variant["id"],
                    "quantity": qty,
                    "title": product.title,
                    "variant_label": _variant_label(variant),
                    "price_cents": int(variant.get("price_cents") or 0),
                })
            else:
                # Product changed/removed after checkout -- keep the raw ref so
                # fulfillment is still possible from the Printify ids.
                line_snapshot.append({
                    "printify_product_id": c.get("p"),
                    "variant_id": c.get("v"),
                    "quantity": qty,
                    "title": "(unavailable)",
                    "variant_label": "",
                    "price_cents": 0,
                })

        cust = obj.get("customer_details") or {}
        collected = obj.get("collected_information") or {}
        ship = (
            (obj.get("shipping_details") or {}).get("address")
            or (collected.get("shipping_details") or {}).get("address")
            or cust.get("address")
            or {}
        )
        ship_name = (
            (obj.get("shipping_details") or {}).get("name")
            or (collected.get("shipping_details") or {}).get("name")
            or cust.get("name")
        )
        rc_user_id = meta.get("rc_user_id")
        try:
            user_id = int(rc_user_id) if rc_user_id else None
        except ValueError:
            user_id = None

        order = ShopOrder(
            order_number=_new_order_number(),
            stripe_session_id=session_id,
            stripe_payment_intent_id=obj.get("payment_intent"),
            user_id=user_id,
            buyer_email=cust.get("email"),
            buyer_name=ship_name or cust.get("name"),
            phone=cust.get("phone"),
            subtotal_cents=int(obj.get("amount_subtotal") or 0),
            shipping_cents=int((obj.get("total_details") or {}).get("amount_shipping") or 0),
            total_cents=int(obj.get("amount_total") or 0),
            currency=obj.get("currency") or "usd",
            ship_line1=ship.get("line1"),
            ship_line2=ship.get("line2"),
            ship_city=ship.get("city"),
            ship_state=ship.get("state"),
            ship_zip=ship.get("postal_code"),
            ship_country=ship.get("country"),
            line_items=json.dumps(line_snapshot),
            status="paid",
        )
        db.add(order)
        db.commit()
        order_id_to_push = order.id
        logger.info("shop: recorded order %s (%s)", order.order_number, session_id)
    except Exception:
        logger.exception("shop webhook: failed to record order for %s", session_id)
        db.rollback()
        # Still 200 so Stripe doesn't hammer us; reconcile from the dashboard.
        return {"received": True, "error": "record_failed"}
    finally:
        db.close()

    if order_id_to_push is not None:
        await run_in_threadpool(_push_order_to_printify, order_id_to_push)
        await run_in_threadpool(_notify_admin_order, order_id_to_push)

    return {"received": True}


def _notify_admin_order(order_id: int) -> None:
    """Email the admin about a new order (fail-soft; send_alert spawns its own
    thread for delivery, so this only reads the order + composes)."""
    try:
        db = SessionLocal()
        try:
            o = db.query(ShopOrder).filter(ShopOrder.id == order_id).one_or_none()
            if not o:
                return
            try:
                items = json.loads(o.line_items) if o.line_items else []
            except Exception:
                items = []
            alerts.emit_shop_order(
                order_number=o.order_number,
                buyer_name=o.buyer_name,
                buyer_email=o.buyer_email,
                total_cents=o.total_cents,
                ship_city=o.ship_city,
                ship_country=o.ship_country,
                items=items,
                status=o.status,
                printify_order_id=o.printify_order_id,
                printify_error=o.printify_error,
            )
            # Customer-facing order confirmation (not pref-gated).
            shop_email.send_order_confirmation(
                to_email=o.buyer_email,
                order_number=o.order_number,
                items=items,
                subtotal_cents=o.subtotal_cents,
                shipping_cents=o.shipping_cents,
                total_cents=o.total_cents,
                ship={
                    "line1": o.ship_line1, "line2": o.ship_line2, "city": o.ship_city,
                    "state": o.ship_state, "zip": o.ship_zip, "country": o.ship_country,
                },
            )
        finally:
            db.close()
    except Exception:
        logger.exception("shop: order notifications failed for order %s", order_id)


# ---------------------------------------------------------------------------
# Printify order-status webhook
# ---------------------------------------------------------------------------

def _verify_printify_sig(raw_body: bytes, header_sig: Optional[str]) -> bool:
    secret = settings.printify_webhook_secret
    if not secret:
        logger.warning("shop: PRINTIFY_WEBHOOK_SECRET unset -- accepting unverified event")
        return True
    if not header_sig:
        return False
    provided = header_sig[7:] if header_sig.startswith("sha256=") else header_sig
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


@router.post("/api/shop/printify-webhook")
async def printify_webhook(request: Request, x_pfy_signature: str = Header(default="")):
    raw = await request.body()
    if not _verify_printify_sig(raw, x_pfy_signature):
        raise HTTPException(400, "Invalid signature")
    try:
        event = json.loads(raw)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    resource = event.get("resource") or {}
    resource_id = resource.get("id")
    if not resource_id:
        return {"received": True, "ignored": "no resource.id"}
    data = resource.get("data") or {}
    event_type = event.get("type") or ""
    status = (data.get("status") or "").lower()
    shipment = (data.get("shipments") or [{}])[0] if data.get("shipments") else {}

    ship_notice: Optional[dict] = None
    db = SessionLocal()
    try:
        order = (
            db.query(ShopOrder)
            .filter(ShopOrder.printify_order_id == resource_id)
            .one_or_none()
        )
        if not order:
            return {"received": True, "ignored": "no local order"}

        cancelled = status in ("cancelled", "canceled")
        if event_type in ("order:created", "order:updated", "order:sent-to-production"):
            if cancelled:
                order.status = "cancelled"
            elif order.status in ("paid", "in_production"):
                order.status = "in_production"
        elif event_type == "order:shipment:created":
            already_shipped = order.status in ("shipped", "delivered")
            order.status = "shipped"
            order.shipped_at = datetime.utcnow()
            order.carrier = shipment.get("carrier")
            order.tracking_number = shipment.get("number")
            order.tracking_url = shipment.get("url")
            # First shipment event -> email the customer their tracking.
            if not already_shipped and order.buyer_email:
                try:
                    items = json.loads(order.line_items) if order.line_items else []
                except Exception:
                    items = []
                ship_notice = {
                    "to_email": order.buyer_email,
                    "order_number": order.order_number,
                    "carrier": order.carrier,
                    "tracking_number": order.tracking_number,
                    "tracking_url": order.tracking_url,
                    "items": items,
                }
        elif event_type == "order:shipment:delivered":
            order.status = "delivered"
            order.delivered_at = datetime.utcnow()
        db.commit()
    except Exception:
        logger.exception("shop printify webhook: DB update failed")
        db.rollback()
        ship_notice = None
    finally:
        db.close()

    if ship_notice:
        shop_email.send_shipping_notice(**ship_notice)

    return {"received": True}


# ---------------------------------------------------------------------------
# Admin (read-only orders view; Site Admin -> Shop -> Orders)
# ---------------------------------------------------------------------------

@admin_router.get("/api/admin/shop/status")
def admin_shop_status(request: Request, admin=Depends(require_admin_session)):
    """Dark-launch state for the admin panel: is the shop open + notify-me count."""
    db = SessionLocal()
    try:
        return {
            "enabled": feature_flags.is_shop_enabled(db),
            "coming_soon_message": feature_flags.shop_coming_soon_message(db),
            "subscriber_count": db.query(ShopSubscriber).count(),
        }
    finally:
        db.close()


class ShopToggleIn(BaseModel):
    enabled: bool


@admin_router.post("/api/admin/shop/toggle")
def admin_shop_toggle(body: ShopToggleIn, request: Request,
                      admin=Depends(require_admin_session)):
    """Open or close the storefront (no redeploy). Off = dark (coming-soon)."""
    db = SessionLocal()
    try:
        feature_flags.set_shop_enabled(db, body.enabled)
        return {"enabled": body.enabled}
    finally:
        db.close()


class ShopNotifyIn(BaseModel):
    force: bool = False
    dry_run: bool = False


@admin_router.post("/api/admin/shop/notify-live")
def admin_notify_live(body: ShopNotifyIn, request: Request,
                      admin=Depends(require_admin_session)):
    """Email the notify-me list that the shop is live (stamps notified_at).
    dry_run just returns the eligible count."""
    db = SessionLocal()
    try:
        return shop_email.notify_shop_live(db, force=body.force, dry_run=body.dry_run)
    finally:
        db.close()


@admin_router.get("/api/admin/shop/subscribers")
def admin_list_subscribers(request: Request, admin=Depends(require_admin_session)):
    """Notify-me list captured while the shop is dark."""
    db = SessionLocal()
    try:
        rows = (
            db.query(ShopSubscriber)
            .order_by(ShopSubscriber.created_at.desc())
            .limit(1000)
            .all()
        )
        return {"subscribers": [
            {"id": s.id, "email": s.email,
             "created_at": s.created_at.isoformat() if s.created_at else None,
             "notified_at": s.notified_at.isoformat() if s.notified_at else None}
            for s in rows
        ]}
    finally:
        db.close()


@admin_router.get("/api/admin/shop/orders")
def admin_list_orders(request: Request, admin=Depends(require_admin_session)):
    """Newest-first order ledger for the admin panel (read-only)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(ShopOrder)
            .order_by(ShopOrder.created_at.desc())
            .limit(300)
            .all()
        )
        out = []
        for o in rows:
            try:
                items = json.loads(o.line_items) if o.line_items else []
            except Exception:
                items = []
            out.append({
                "id": o.id,
                "order_number": o.order_number,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "status": o.status,
                "buyer_name": o.buyer_name,
                "buyer_email": o.buyer_email,
                "phone": o.phone,
                "ship": {
                    "line1": o.ship_line1, "line2": o.ship_line2, "city": o.ship_city,
                    "state": o.ship_state, "zip": o.ship_zip, "country": o.ship_country,
                },
                "subtotal_cents": o.subtotal_cents,
                "shipping_cents": o.shipping_cents,
                "total_cents": o.total_cents,
                "currency": o.currency,
                "items": items,
                "printify_order_id": o.printify_order_id,
                "printify_error": o.printify_error,
                "carrier": o.carrier,
                "tracking_number": o.tracking_number,
                "tracking_url": o.tracking_url,
            })
        return {"orders": out}
    finally:
        db.close()
