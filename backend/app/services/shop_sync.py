"""Sync Printify products into shop_products.

Ports chadlewine's lib/printify-sync.ts (trimmed: images are stored inline as a
JSON list on the row instead of a separate gallery table). Printify is the
source of truth for title/description/images/variants/price; RC keeps a stable
slug + display order + status. Idempotent: matches on printify_product_id,
updates in place, inserts new products with the next display_order.

Enabled variants only. Each variant is normalized to
  {"id","title","size","color","price_cents"}
and the row's `price` is the lowest enabled-variant price in dollars.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import ShopProduct
from app.services import printify_service

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "product"


def _strip_html(raw: str) -> str:
    # Drop tags, decode entities (&nbsp; &amp; ...), collapse whitespace.
    text = re.sub(r"<[^>]*>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _pick_image(p: dict) -> str | None:
    images = p.get("images") or []
    for img in images:
        if img.get("is_default") and img.get("src"):
            return img["src"]
    return images[0]["src"] if images and images[0].get("src") else None


def _collect_images(p: dict) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    images = p.get("images") or []
    default = next((i for i in images if i.get("is_default") and i.get("src")), None)
    if default:
        seen.add(default["src"])
        out.append(default["src"])
    for img in images:
        src = img.get("src")
        if not src or src in seen:
            continue
        seen.add(src)
        out.append(src)
    return out


def _front_image_by_variant(p: dict) -> dict[int, str]:
    """Map each variant id -> its front mockup src, so a color can show its own
    shirt. Printify tags each image with the variant_ids it depicts + a
    position; we take the first front-facing image per variant."""
    m: dict[int, str] = {}
    for im in p.get("images") or []:
        if im.get("position") == "front" and im.get("src"):
            for vid in im.get("variant_ids") or []:
                m.setdefault(vid, im["src"])
    return m


def _normalize_variants(p: dict) -> list[dict]:
    # Map every option value id -> (group name, label) so we can pull size/color.
    value_lookup: dict[int, tuple[str, str]] = {}
    for group in p.get("options") or []:
        for v in group.get("values") or []:
            value_lookup[v["id"]] = (group.get("name", ""), v.get("title", ""))

    front_by_variant = _front_image_by_variant(p)
    out: list[dict] = []
    for v in p.get("variants") or []:
        if not v.get("is_enabled"):
            continue
        size = None
        color = None
        for option_id in v.get("options") or []:
            meta = value_lookup.get(option_id)
            if not meta:
                continue
            gname = meta[0].lower()
            if "size" in gname:
                size = meta[1]
            elif "color" in gname or "colour" in gname:
                color = meta[1]
        title = v.get("title", "")
        if not size and " / " in title:
            parts = [x.strip() for x in title.split(" / ")]
            if len(parts) == 2:
                color = color or parts[0]
                size = parts[1]
        out.append(
            {
                "id": v["id"],
                "title": title,
                "size": size,
                "color": color,
                "price_cents": v.get("price", 0),
                # Per-variant front mockup so the color acts as its own SKU with
                # its own shirt image.
                "image": front_by_variant.get(v["id"]),
            }
        )
    return out


def sync_printify_products(db: Session) -> dict:
    """Pull the shop's products from Printify and upsert into shop_products.
    Returns {ok, fetched, created, updated, errors:[...]}."""
    if not printify_service.is_configured():
        return {"ok": False, "fetched": 0, "created": 0, "updated": 0,
                "errors": ["Printify not configured"]}

    try:
        envelope = printify_service.get_shop_products()
    except printify_service.PrintifyError as e:
        logger.error("shop sync: Printify fetch failed: %s", e)
        return {"ok": False, "fetched": 0, "created": 0, "updated": 0, "errors": [str(e)]}

    products = envelope.get("data") or []
    now = datetime.now(timezone.utc)
    created = 0
    updated = 0
    errors: list[str] = []

    # Next display_order = end of the current list (new products sort last).
    max_order = db.query(ShopProduct).count()

    for p in products:
        try:
            variants = _normalize_variants(p)
            if not variants:
                continue  # nothing purchasable
            lowest_cents = min(v["price_cents"] for v in variants)
            price = lowest_cents / 100.0
            image_url = _pick_image(p)
            image_urls = _collect_images(p)
            title = p.get("title", "Untitled")
            description = _strip_html(p.get("description", ""))
            status = "active" if p.get("visible") else "inactive"

            existing = (
                db.query(ShopProduct)
                .filter(ShopProduct.printify_product_id == p["id"])
                .one_or_none()
            )
            if existing:
                existing.title = title
                existing.description = description
                existing.image_url = image_url
                existing.image_urls = json.dumps(image_urls)
                existing.price = price
                existing.variants = json.dumps(variants)
                existing.status = status
                existing.last_synced_at = now
                updated += 1
            else:
                base = _slugify(title)
                slug = base
                n = 2
                while db.query(ShopProduct).filter(ShopProduct.slug == slug).first():
                    slug = f"{base}-{n}"
                    n += 1
                    if n > 50:
                        break
                db.add(
                    ShopProduct(
                        printify_product_id=p["id"],
                        slug=slug,
                        title=title,
                        description=description,
                        image_url=image_url,
                        image_urls=json.dumps(image_urls),
                        price=price,
                        variants=json.dumps(variants),
                        status=status,
                        display_order=max_order,
                        last_synced_at=now,
                    )
                )
                max_order += 1
                created += 1
        except Exception as e:  # noqa: BLE001 -- one bad product must not abort the sync
            logger.exception("shop sync: failed on product %s", p.get("id"))
            errors.append(f"{p.get('id')}: {e}")

    db.commit()
    return {
        "ok": len(errors) == 0,
        "fetched": len(products),
        "created": created,
        "updated": updated,
        "errors": errors,
    }
