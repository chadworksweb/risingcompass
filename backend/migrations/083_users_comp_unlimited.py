"""Add users.comp_unlimited -- admin-granted unlimited Lyrical Charger comp.

A per-user boolean comp flag, orthogonal to subscription_tier (a comped user
keeps tier='free' and has no Stripe subscription). When true, the billing
service treats every Charger run (single-song + album) as zero-cost and the
calibrate rate-limiter lifts the per-user daily backstop. Library entitlement
is unchanged -- comp is Charger-only by design.

Spend/grant accounting is unaffected: comped runs write delta=0 'comp' ledger
rows so usage stays auditable without disturbing the
row-buckets == signed-ledger-sum invariant.

PG-compatible (063+). Base.metadata.create_all() picks the column up on fresh
installs from the updated User model; this migration is the idempotent path
for the existing prod DB.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS comp_unlimited "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    ))
