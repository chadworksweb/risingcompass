"""Add billing/subscription columns + credit buckets to users.

M1 of the monetization build. Two-bucket model:
  - allowance_credits: expiring monthly grant (reset on invoice.paid)
  - purchased_credits: permanent pack-purchased credits

Spend order: allowance first, then purchased. The row columns are the
denormalised fast-read; credit_ledger (migration 073) is the source of truth.

subscription_tier values: 'free' | 'plus' | 'pro'.
subscription_status values: 'active' | 'past_due' | 'canceled' (or NULL).

PG-compatible (063+). Base.metadata.create_all() picks these up on fresh
installs from the updated User model; this migration is the idempotent path
for the existing prod DB.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT"
    ))
    conn.execute(text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT"
    ))
    conn.execute(text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_tier "
        "VARCHAR(30) NOT NULL DEFAULT 'free'"
    ))
    conn.execute(text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(20)"
    ))
    conn.execute(text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_period_end TIMESTAMP"
    ))
    conn.execute(text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS allowance_credits "
        "INTEGER NOT NULL DEFAULT 0"
    ))
    conn.execute(text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS purchased_credits "
        "INTEGER NOT NULL DEFAULT 0"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_users_stripe_customer "
        "ON users(stripe_customer_id)"
    ))
