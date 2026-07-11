"""Customer-facing shop emails (order confirmation + shipping notice).

Distinct from services/alerts.py, which emails the ADMIN (gated on
admin_alert_prefs). These go to the BUYER and are never pref-gated -- a paid
order always earns a confirmation. Delivery reuses alerts._send_resend (the
generic Resend POST) and runs on a daemon thread so the webhook is never
blocked. Fail-soft: no email address or no Resend key -> silently skipped.
"""

from __future__ import annotations

import logging
import threading
from html import escape
from typing import Optional

from app.config import settings
from app.services.alerts import _send_resend

logger = logging.getLogger(__name__)

_BRAND = "#008f72"


def _money(cents: int) -> str:
    return f"${(cents or 0) / 100:.2f}"


def _send_async(to_email: str, subject: str, html: str) -> None:
    if not to_email or not settings.resend_api_key:
        return

    def _run():
        try:
            _send_resend(to_email, subject, html)
        except Exception:
            logger.exception("shop customer email failed (subject=%s)", subject)

    threading.Thread(target=_run, daemon=True, name="shop-email").start()


def _items_table(items: list) -> str:
    rows = "".join(
        f'<tr>'
        f'<td style="padding:6px 12px 6px 0;color:#333;font-size:14px;">{escape(str(i.get("title") or ""))}'
        f'{(" &middot; " + escape(str(i.get("variant_label")))) if i.get("variant_label") else ""}</td>'
        f'<td style="padding:6px 0;color:#555;font-size:14px;text-align:right;">x{i.get("quantity", 1)}</td>'
        f'</tr>'
        for i in items
    )
    return f'<table style="border-collapse:collapse;width:100%;margin:0 0 16px;">{rows}</table>'


def _address_block(ship: dict) -> str:
    parts = [
        ship.get("line1"), ship.get("line2"),
        " ".join(x for x in [ship.get("city"), ship.get("state"), ship.get("zip")] if x),
        ship.get("country"),
    ]
    lines = "<br>".join(escape(p) for p in parts if p)
    return lines or "(no address)"


def send_order_confirmation(*, to_email: Optional[str], order_number: str,
                            items: list, subtotal_cents: int, shipping_cents: int,
                            total_cents: int, ship: dict) -> None:
    if not to_email:
        return
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 560px; margin:0 auto; color:#222;">
      <p style="font-size:16px;margin:0 0 4px;"><strong>Thank you for your order.</strong></p>
      <p style="font-size:14px;color:#555;margin:0 0 20px;">Order <strong>{escape(order_number)}</strong> is confirmed. It prints on demand and ships once production wraps. You'll get a tracking link when it goes out.</p>
      {_items_table(items)}
      <table style="border-collapse:collapse;width:100%;font-size:14px;color:#333;border-top:1px solid #eee;">
        <tr><td style="padding:6px 0;color:#666;">Subtotal</td><td style="padding:6px 0;text-align:right;">{_money(subtotal_cents)}</td></tr>
        <tr><td style="padding:6px 0;color:#666;">Shipping</td><td style="padding:6px 0;text-align:right;">{_money(shipping_cents)}</td></tr>
        <tr><td style="padding:8px 0;font-weight:bold;border-top:1px solid #eee;">Total</td><td style="padding:8px 0;text-align:right;font-weight:bold;border-top:1px solid #eee;">{_money(total_cents)}</td></tr>
      </table>
      <p style="font-size:13px;color:#666;margin:20px 0 4px;"><strong>Shipping to</strong></p>
      <p style="font-size:13px;color:#555;margin:0 0 20px;line-height:1.5;">{_address_block(ship)}</p>
      <p style="font-size:12px;color:#999;margin:24px 0 0;border-top:1px solid #eee;padding-top:12px;">The Rising Compass &middot; risingcompass.net</p>
    </div>
    """
    _send_async(to_email, f"Your Rising Compass order {order_number} is confirmed", html)


def send_shipping_notice(*, to_email: Optional[str], order_number: str,
                         carrier: Optional[str], tracking_number: Optional[str],
                         tracking_url: Optional[str], items: list) -> None:
    if not to_email:
        return
    if tracking_url:
        track = (f'<p style="margin:0 0 20px;"><a href="{escape(tracking_url)}" '
                 f'style="display:inline-block;background:{_BRAND};color:#fff;text-decoration:none;'
                 f'padding:10px 18px;border-radius:4px;font-size:14px;">Track your package</a></p>')
    elif tracking_number:
        track = f'<p style="font-size:14px;color:#333;margin:0 0 20px;">{escape(carrier or "Carrier")}: <strong>{escape(tracking_number)}</strong></p>'
    else:
        track = ""
    carrier_line = (f'<p style="font-size:13px;color:#666;margin:0 0 16px;">'
                    f'{escape(carrier)}{(" &middot; " + escape(tracking_number)) if tracking_number else ""}</p>'
                    if carrier else "")
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 560px; margin:0 auto; color:#222;">
      <p style="font-size:16px;margin:0 0 4px;"><strong>Your order is on its way.</strong></p>
      <p style="font-size:14px;color:#555;margin:0 0 20px;">Order <strong>{escape(order_number)}</strong> has shipped.</p>
      {carrier_line}
      {track}
      {_items_table(items)}
      <p style="font-size:12px;color:#999;margin:24px 0 0;border-top:1px solid #eee;padding-top:12px;">The Rising Compass &middot; risingcompass.net</p>
    </div>
    """
    _send_async(to_email, f"Your Rising Compass order {order_number} has shipped", html)
