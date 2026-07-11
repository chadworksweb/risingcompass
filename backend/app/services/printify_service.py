"""Printify API wrapper for the RC shop.

Ports the pieces of chadlewine's lib/printify.ts that the RC storefront needs:
read the shop's products (for the sync), quote destination-aware shipping, and
create + advance an order on purchase. Uses a short-lived synchronous
httpx.Client per call (httpx is already a dependency); async endpoints call
these via run_in_threadpool so the blocking HTTP never wedges the event loop.

Runs against the dedicated Rising Compass Printify "custom integration" shop.
Token + shop id come from settings (PRINTIFY_API_TOKEN / PRINTIFY_SHOP_ID).
Every function raises PrintifyError (a plain RuntimeError subclass) on a
non-2xx so callers can map it to a 502 / fail soft.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

PRINTIFY_API = "https://api.printify.com/v1"
_TIMEOUT = 20.0


class PrintifyError(RuntimeError):
    """Any non-2xx from Printify, or a missing-config error."""


def is_configured() -> bool:
    return bool(settings.printify_api_token and settings.printify_shop_id)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.printify_api_token}",
        "Content-Type": "application/json",
    }


def _shop_id() -> str:
    return settings.printify_shop_id


def _request(method: str, path: str, *, json: Any = None) -> Any:
    if not is_configured():
        raise PrintifyError("Printify is not configured (token/shop id missing)")
    url = f"{PRINTIFY_API}{path}"
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.request(method, url, headers=_headers(), json=json)
    except httpx.HTTPError as e:
        raise PrintifyError(f"Printify request failed: {e}") from e
    if resp.status_code < 200 or resp.status_code >= 300:
        # Keep the body short in logs (AUP output hygiene) but pass the status.
        body = (resp.text or "")[:300]
        raise PrintifyError(f"Printify error {resp.status_code}: {body}")
    if resp.content:
        return resp.json()
    return None


# --- Catalog / products -----------------------------------------------------

def get_shop_products() -> dict:
    """GET /shops/{id}/products.json -- all products in the shop (paged; the RC
    shop is tiny, so the first page is the whole catalog). Returns Printify's
    envelope: {"data": [ ... ], ...}."""
    return _request("GET", f"/shops/{_shop_id()}/products.json") or {"data": []}


# --- Shipping quote ---------------------------------------------------------

def get_order_shipping_cost(*, line_items: list[dict], address_to: dict) -> dict:
    """POST /shops/{id}/orders/shipping.json -- the destination-aware quote
    Printify itself would bill us, in cents. `standard` is always present.

    line_items: [{"product_id", "variant_id", "quantity"}]
    address_to: {"first_name","last_name","email","country","region",
                 "address1","address2"?,"city","zip"}
    """
    return _request(
        "POST",
        f"/shops/{_shop_id()}/orders/shipping.json",
        json={"line_items": line_items, "address_to": address_to},
    )


# --- Orders -----------------------------------------------------------------

def create_order(payload: dict) -> dict:
    """POST /shops/{id}/orders.json -- create an order (draft). Returns {"id"}.

    payload: {external_id, label?, line_items:[{product_id,variant_id,quantity}],
              shipping_method (1=standard), send_shipping_notification, address_to}
    """
    return _request("POST", f"/shops/{_shop_id()}/orders.json", json=payload)


def send_order_to_production(printify_order_id: str) -> None:
    """POST .../orders/{id}/send_to_production.json -- moves the draft into
    Printify's fulfillment queue so it actually prints + ships."""
    _request(
        "POST",
        f"/shops/{_shop_id()}/orders/{printify_order_id}/send_to_production.json",
    )


def get_order(printify_order_id: str) -> dict:
    """GET .../orders/{id}.json -- read an order (status + shipments)."""
    return _request("GET", f"/shops/{_shop_id()}/orders/{printify_order_id}.json")
