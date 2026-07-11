"""Shop (Printify merch storefront at /shop/) -- product cache + order ledger.

Ports the core of chadlewine.com's ecom setup to Rising Compass:

  - shop_products -- the 3 (and future) Printify products synced into Postgres
    so the grid + detail pages read locally instead of hitting Printify per
    request. Printify is the source of truth; RC owns a stable slug + display
    order + status. `variants` / `image_urls` are JSON-encoded Text (RC's
    per-row JSON convention).
  - shop_orders -- one row per completed purchase, written by the Stripe cart
    webhook and pushed to Printify (auto sent to production), then advanced by
    the Printify order-status webhook. Money in cents.

PG-compatible (063+). Base.metadata.create_all() builds these on fresh installs
from the models in app/models.py; this creates them on existing DBs. Idempotent
(CREATE TABLE / INDEX IF NOT EXISTS).
"""

from sqlalchemy import text


def up(conn):
    # --- product cache -------------------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS shop_products (
            id                  SERIAL PRIMARY KEY,
            printify_product_id TEXT NOT NULL UNIQUE,
            slug                TEXT NOT NULL UNIQUE,
            title               TEXT NOT NULL,
            description         TEXT,
            image_url           TEXT,
            image_urls          TEXT,
            price               DOUBLE PRECISION,
            variants            TEXT,
            status              TEXT NOT NULL DEFAULT 'active',
            display_order       INTEGER NOT NULL DEFAULT 0,
            last_synced_at      TIMESTAMP,
            created_at          TIMESTAMP DEFAULT (now() at time zone 'utc')
        )
    """))
    # The grid lists active products in display order.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_shop_products_status_order "
        "ON shop_products (status, display_order)"
    ))

    # --- order ledger --------------------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS shop_orders (
            id                       SERIAL PRIMARY KEY,
            order_number             TEXT NOT NULL UNIQUE,
            stripe_session_id        TEXT NOT NULL UNIQUE,
            stripe_payment_intent_id TEXT,
            user_id                  INTEGER REFERENCES users(id) ON DELETE SET NULL,
            buyer_email              TEXT,
            buyer_name               TEXT,
            phone                    TEXT,
            subtotal_cents           INTEGER NOT NULL DEFAULT 0,
            shipping_cents           INTEGER NOT NULL DEFAULT 0,
            total_cents              INTEGER NOT NULL DEFAULT 0,
            currency                 TEXT NOT NULL DEFAULT 'usd',
            ship_line1               TEXT,
            ship_line2               TEXT,
            ship_city                TEXT,
            ship_state               TEXT,
            ship_zip                 TEXT,
            ship_country             TEXT,
            line_items               TEXT,
            status                   TEXT NOT NULL DEFAULT 'paid',
            printify_order_id        TEXT,
            printify_error           TEXT,
            carrier                  TEXT,
            tracking_number          TEXT,
            tracking_url             TEXT,
            created_at               TIMESTAMP DEFAULT (now() at time zone 'utc'),
            pushed_to_printify_at    TIMESTAMP,
            shipped_at               TIMESTAMP,
            delivered_at             TIMESTAMP
        )
    """))
    # Order-status webhook looks up by the Printify order id.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_shop_orders_printify "
        "ON shop_orders (printify_order_id)"
    ))
    # A buyer's order history (anonymous = by email).
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_shop_orders_email "
        "ON shop_orders (buyer_email)"
    ))

    # --- coming-soon / notify-me list (dark launch) --------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS shop_subscribers (
            id          SERIAL PRIMARY KEY,
            email       TEXT NOT NULL UNIQUE,
            notified_at TIMESTAMP,
            created_at  TIMESTAMP DEFAULT (now() at time zone 'utc')
        )
    """))
