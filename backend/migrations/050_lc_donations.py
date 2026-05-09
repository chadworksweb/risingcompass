"""Lyrical Charger donations: persist Stripe Checkout sessions.

The patronage flow on RC mirrors chadlewine.com — same Stripe account,
own webhook endpoint. Each Checkout session creates a pending row
here; the webhook flips it to 'succeeded' (or 'failed') with the final
amount and customer email.

`source` is plain text — 'lyrical_charger' for now, but we can route
future donate buttons (homepage tip jar, etc.) to the same table by
tagging them differently.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS rc_donations ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  stripe_session_id TEXT NOT NULL UNIQUE,"
        "  amount_cents INTEGER NOT NULL,"
        "  currency TEXT NOT NULL DEFAULT 'usd',"
        "  status TEXT NOT NULL DEFAULT 'pending',"
        "  source TEXT,"
        "  customer_email TEXT,"
        "  payment_intent_id TEXT,"
        "  created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        "  completed_at TEXT"
        ")"
    ))
