"""Create credit_ledger -- source of truth for every credit grant + spend.

bucket values: 'allowance' | 'purchased' | 'rejected' | 'settlement'.
delta is signed: negative for spend, positive for grants/purchases/refunds.

UNIQUE(reason, ref_id, bucket) WHERE ref_id IS NOT NULL gates against
replayed grants (Stripe webhook replays of the same event id). Bucket is
included so a single charge can write per-bucket rows (allowance + purchased)
under the same reason+ref_id without tripping the constraint -- bucket is
always known per-grant so this never weakens grant idempotency.

reason vocabulary (kept open-ended; new reasons just need unique ref_ids):
  signup_grant   - one-time signup welcome
  monthly_grant  - tier allowance reset on invoice.paid
  pack_purchase  - credit pack purchased via Checkout
  song_miss      - calibrate-* engine run, cache miss (full Opus)
  song_cache_hit - calibrate-* engine run, cache hit (cheap)
  hold           - reserve credits for an async job (album charger)
  settle         - reconcile a hold against actual cost (refund or extra)
  refund         - manual refund

context_json carries event-specific context (song id, track count, Stripe
event id, etc.) for the admin ledger view without growing the column set.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS credit_ledger (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            delta INTEGER NOT NULL,
            bucket VARCHAR(20) NOT NULL,
            reason VARCHAR(40) NOT NULL,
            ref_type VARCHAR(40),
            ref_id TEXT,
            context_json TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_ledger_reason_ref_bucket "
        "ON credit_ledger(reason, ref_id, bucket) WHERE ref_id IS NOT NULL"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_credit_ledger_user_created "
        "ON credit_ledger(user_id, created_at DESC)"
    ))
