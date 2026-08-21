"""Credit metering core.

Two buckets per user (rows on users):
  - allowance_credits: expiring monthly grant (reset on invoice.paid)
  - purchased_credits: permanent pack-purchased credits

Spend order: allowance first, then purchased.

credit_ledger is the source of truth -- the row columns are denormalised
fast reads. Every grant + spend writes a ledger row; the partial unique
index UNIQUE(reason, ref_id, bucket) WHERE ref_id IS NOT NULL gates against
double-grants when Stripe webhooks replay (the same idempotency pattern
donate.py uses for donation upserts).

Concurrency: charge_credits + grant_credits do SELECT ... FOR UPDATE on
the user row, serialising concurrent spends/grants for the same user.
Postgres MVCC tolerates the long Opus transaction (CLAUDE.md note); the
user-row lock is held for the duration of the WRITE phase only, never
across the Opus call -- analyzer.py already structures its read -> AI ->
write split for exactly this reason.

Inline writes on rejection: when check_credits raises 402 it ALSO writes
the rejected attempt to the ledger inline, because FastAPI BackgroundTasks
are dropped when the response is a non-200 (same constraint analyzer.py
handles for error lc_events).
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import CreditLedger, User

logger = logging.getLogger(__name__)

# Free-tier daily-charge passes are recorded as delta=0 ledger rows in this
# bucket/reason. delta=0 keeps the balance-equals-ledger-sum invariant intact
# (same family as 'rejected'/'settlement'/'overrun'); "used today" is a count
# of these rows since UTC midnight, so the allotment self-resets with no job.
DAILY_FREE_BUCKET = "daily_free"
DAILY_FREE_REASON = "daily_free"

# Comped (admin unlimited) Charger runs are recorded as delta=0 rows in this
# bucket/reason -- same delta=0 family as 'daily_free'/'rejected'/'settlement',
# so the balance == signed-ledger-sum invariant is preserved while keeping the
# run auditable. A comped user is never gated by credits or daily-free passes.
COMP_BUCKET = "comp"
COMP_REASON = "comp_unlimited"


def is_unlimited(user: User) -> bool:
    """True if the user carries the admin-granted unlimited Charger comp."""
    return bool(getattr(user, "comp_unlimited", False))


def _record_comp_run(
    db,
    user_id: int,
    *,
    ref_type: str,
    ref_id: Optional[str],
    context: Optional[dict],
) -> None:
    """Write the delta=0 'comp' audit row for a comped (free) Charger run.
    Idempotent on (reason, ref_id, bucket) like every other ledger write."""
    db.add(CreditLedger(
        user_id=user_id, delta=0, bucket=COMP_BUCKET,
        reason=COMP_REASON, ref_type=ref_type, ref_id=ref_id,
        context_json=json.dumps(context) if context else None,
    ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


# --- Free-tier daily allotment -------------------------------------------

def _utc_day_start() -> datetime:
    """UTC midnight of the current day -- the daily-free reset boundary."""
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _daily_free_eligible(user: User) -> bool:
    """Only free-tier accounts get the daily-free allotment. Paid tiers
    (plus/pro) spend allowance -> purchased as usual."""
    from app import billing_config
    return not billing_config.is_paid_user(user.subscription_tier)


def _free_daily_limit(db) -> int:
    """Admin-tunable free-tier daily-charge count (system_flags, defaults to
    billing_config.DAILY_FREE_CHARGES)."""
    from app.services.feature_flags import lyrical_charger_free_daily_charges
    return lyrical_charger_free_daily_charges(db)


def daily_free_used_today(db, user_id: int) -> int:
    """Count of free-tier daily passes this user has consumed since UTC
    midnight. Indexed by (user_id, created_at)."""
    return (
        db.query(func.count(CreditLedger.id))
        .filter(CreditLedger.user_id == user_id)
        .filter(CreditLedger.bucket == DAILY_FREE_BUCKET)
        .filter(CreditLedger.created_at >= _utc_day_start())
        .scalar()
    ) or 0


def daily_free_status(user: User) -> dict:
    """Daily-free usage snapshot for the wallet UI / admin. Paid users report
    a zero allotment (not eligible)."""
    eligible = _daily_free_eligible(user)
    db = SessionLocal()
    try:
        limit = _free_daily_limit(db) if eligible else 0
        used = daily_free_used_today(db, user.id) if eligible else 0
    finally:
        db.close()
    return {
        "daily_free_eligible": eligible,
        "daily_free_limit": limit,
        "daily_free_used": used,
        "daily_free_remaining": max(0, limit - used),
        "daily_free_resets_at": (_utc_day_start() + timedelta(days=1)).isoformat() + "Z",
    }


# --- Read helpers (no DB write) -------------------------------------------

def total_credits(user: User) -> int:
    """Spendable credits across both buckets."""
    return (user.allowance_credits or 0) + (user.purchased_credits or 0)


def wallet_snapshot(user: User) -> dict:
    """Public wallet view: tier + buckets + status. Used by GET /api/billing/me."""
    return {
        "tier": user.subscription_tier or "free",
        "status": user.subscription_status,
        "period_end": user.subscription_period_end.isoformat() if user.subscription_period_end else None,
        "allowance_credits": user.allowance_credits or 0,
        "purchased_credits": user.purchased_credits or 0,
        "total_credits": total_credits(user),
        "comp_unlimited": is_unlimited(user),
    }


# --- Pre-flight ----------------------------------------------------------

def check_credits(
    user_id: int,
    cost: int,
    *,
    reason: str = "preflight",
    allow_daily_free: bool = False,
) -> None:
    """Raise 402 if `user_id` can't afford `cost`. Inline-writes a rejected
    ledger row so the rejection shows up in the admin view even though the
    response is a non-200 (BackgroundTasks won't fire).

    No-op if cost <= 0.

    allow_daily_free=True (single-song charger preflight): a free-tier user
    with a remaining daily-free pass is admitted regardless of credit balance
    -- the pass is consumed later by charge_song on success.
    """
    if cost <= 0:
        return

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user_id).first()
        if not u:
            raise HTTPException(status_code=401, detail="User not found")
        if is_unlimited(u):
            # Comped: never gated by balance. The post-success charge_song /
            # charge_credits writes the delta=0 audit row.
            return
        if allow_daily_free and _daily_free_eligible(u):
            if daily_free_used_today(db, user_id) < _free_daily_limit(db):
                return
        balance = total_credits(u)
        if balance < cost:
            db.add(CreditLedger(
                user_id=user_id, delta=0, bucket="rejected",
                reason=reason, ref_type="preflight",
                context_json=json.dumps({"cost": cost, "balance": balance}),
            ))
            db.commit()
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "insufficient_credits",
                    "cost": cost,
                    "balance": balance,
                    "tier": u.subscription_tier or "free",
                },
            )
    finally:
        db.close()


# --- Spend ---------------------------------------------------------------

def charge_credits(
    user_id: int,
    cost: int,
    *,
    reason: str,
    ref_type: str,
    ref_id: Optional[str] = None,
    context: Optional[dict] = None,
) -> dict:
    """Atomically debit `cost` credits. Allowance bucket first, then purchased.

    Returns {allowance_spent, purchased_spent, new_balance}. Raises 402 if
    the balance is insufficient at the moment of the lock (a concurrent
    spend that drained the balance between check and charge).

    Postgres MVCC + SELECT ... FOR UPDATE on the user row makes concurrent
    spends serialise correctly. The lock is local to the write txn.
    """
    if cost <= 0:
        return {"allowance_spent": 0, "purchased_spent": 0, "new_balance": None}

    db = SessionLocal()
    try:
        u = (
            db.query(User)
            .filter(User.id == user_id)
            .with_for_update()
            .first()
        )
        if not u:
            raise HTTPException(status_code=401, detail="User not found")

        if is_unlimited(u):
            # Comped: no debit. Release the row lock, then write the delta=0
            # audit row (ref_id keeps it idempotent against same-song replays).
            db.rollback()
            cdb = SessionLocal()
            try:
                _record_comp_run(
                    cdb, user_id, ref_type=ref_type, ref_id=ref_id, context=context,
                )
            finally:
                cdb.close()
            return {
                "allowance_spent": 0, "purchased_spent": 0,
                "new_balance": (u.allowance_credits or 0) + (u.purchased_credits or 0),
                "comped": True,
            }

        allowance = u.allowance_credits or 0
        purchased = u.purchased_credits or 0
        if allowance + purchased < cost:
            db.rollback()
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "insufficient_credits",
                    "cost": cost,
                    "balance": allowance + purchased,
                    "tier": u.subscription_tier or "free",
                },
            )

        from_allowance = min(allowance, cost)
        from_purchased = cost - from_allowance

        u.allowance_credits = allowance - from_allowance
        u.purchased_credits = purchased - from_purchased

        ctx_json = json.dumps(context) if context else None
        if from_allowance > 0:
            db.add(CreditLedger(
                user_id=user_id, delta=-from_allowance, bucket="allowance",
                reason=reason, ref_type=ref_type, ref_id=ref_id, context_json=ctx_json,
            ))
        if from_purchased > 0:
            db.add(CreditLedger(
                user_id=user_id, delta=-from_purchased, bucket="purchased",
                reason=reason, ref_type=ref_type, ref_id=ref_id, context_json=ctx_json,
            ))

        try:
            db.commit()
        except IntegrityError:
            # A concurrent request already wrote this exact (reason, ref_id,
            # bucket) charge -- e.g. a double-submit of the same song resolves
            # to the same submitted.id. The partial unique index makes the
            # second commit collide; roll back (which also reverts this txn's
            # row decrement, so no double-charge) and treat it as an
            # idempotent replay so the caller still returns the
            # already-persisted result instead of surfacing a 500.
            db.rollback()
            logger.info(
                "charge_credits replay ignored: reason=%s ref_id=%s", reason, ref_id,
            )
            return {"allowance_spent": 0, "purchased_spent": 0, "new_balance": None, "replayed": True}
        return {
            "allowance_spent": from_allowance,
            "purchased_spent": from_purchased,
            "new_balance": (u.allowance_credits or 0) + (u.purchased_credits or 0),
        }
    finally:
        db.close()


def charge_song(
    user_id: int,
    cost: int,
    *,
    reason: str,
    ref_type: str,
    ref_id: Optional[str] = None,
    context: Optional[dict] = None,
) -> dict:
    """Single-song charge with free-tier daily-pass priority.

    A free-tier user with a daily-free pass left for the UTC day consumes one
    pass (a delta=0 'daily_free' ledger row -- no bucket debit) instead of
    spending credits. Once the daily allotment is exhausted, and for ALL paid
    users, this falls through to charge_credits (allowance -> purchased).

    Returns the usual charge dict plus 'source' in {'daily_free','credits','free'}.
    The user-row lock serialises the count+insert so concurrent runs for the
    same user can't over-grant passes; the (reason,ref_id,bucket) unique index
    makes a same-song replay idempotent.
    """
    if cost <= 0:
        return {"source": "free", "allowance_spent": 0, "purchased_spent": 0, "new_balance": None}

    db = SessionLocal()
    try:
        u = (
            db.query(User)
            .filter(User.id == user_id)
            .with_for_update()
            .first()
        )
        if not u:
            raise HTTPException(status_code=401, detail="User not found")
        if is_unlimited(u):
            # Comped: zero-cost run. Skip the daily-free machinery entirely so
            # comped runs never consume a daily pass; record the audit row.
            _record_comp_run(
                db, user_id, ref_type=ref_type, ref_id=ref_id, context=context,
            )
            return {"source": "comp", "allowance_spent": 0,
                    "purchased_spent": 0, "new_balance": None}
        if _daily_free_eligible(u):
            limit = _free_daily_limit(db)
            used = daily_free_used_today(db, user_id)
            if used < limit:
                db.add(CreditLedger(
                    user_id=user_id, delta=0, bucket=DAILY_FREE_BUCKET,
                    reason=DAILY_FREE_REASON, ref_type=ref_type, ref_id=ref_id,
                    context_json=json.dumps({
                        **(context or {}),
                        "daily_free_used": used + 1, "daily_free_limit": limit,
                    }),
                ))
                try:
                    db.commit()
                except IntegrityError:
                    # Same-song double-submit: the pass was already recorded.
                    db.rollback()
                    logger.info("charge_song daily_free replay ignored: ref_id=%s", ref_id)
                    return {"source": "daily_free", "allowance_spent": 0,
                            "purchased_spent": 0, "new_balance": None, "replayed": True}
                return {"source": "daily_free", "allowance_spent": 0,
                        "purchased_spent": 0, "new_balance": None}
        # No free pass available (allotment spent, or a paid user): release the
        # row lock without writing, then debit credits via charge_credits
        # (which takes its own lock + handles the 402 / replay paths).
        db.rollback()
    finally:
        db.close()

    res = charge_credits(
        user_id, cost,
        reason=reason, ref_type=ref_type, ref_id=ref_id, context=context,
    )
    res["source"] = "credits"
    return res


def record_unbilled_overrun(
    user_id: int,
    cost: int,
    *,
    ref_type: str,
    ref_id: Optional[str] = None,
    context: Optional[dict] = None,
) -> None:
    """Audit marker (delta=0) for a successful engine run that could NOT be
    billed because a concurrent spend drained the wallet between the
    (unlocked) pre-flight and the post-success charge. The result is
    delivered anyway (the work is already done); this row makes the
    uncompensated run visible to admin/reconciliation without disturbing the
    balance-equals-ledger-sum invariant (delta=0).
    """
    db = SessionLocal()
    try:
        db.add(CreditLedger(
            user_id=user_id, delta=0, bucket="overrun",
            reason="unbilled_overrun", ref_type=ref_type, ref_id=ref_id,
            context_json=json.dumps({"cost": cost, **(context or {})}),
        ))
        db.commit()
    except IntegrityError:
        db.rollback()
    except Exception:
        db.rollback()
        logger.exception("record_unbilled_overrun failed user=%s", user_id)
    finally:
        db.close()


def refund_song(
    user_id: int,
    charge_result: dict,
    *,
    ref_id: str,
    context: Optional[dict] = None,
) -> bool:
    """Reverse a single-song charge by crediting back exactly what was debited.

    Used only when a run was charged but failed to produce a saved song (see
    analyzer.py). With the current ordering the charge happens AFTER the song is
    committed, so a charge implies delivery -- this is the defensive safety net
    that keeps "charged but undelivered" impossible by construction (and guards
    any future reordering). The live recovery path is the salvage card, not this.

    Reverses the (allowance/purchased) split from `charge_result` with positive
    ledger rows into the same buckets and bumps the denormalised user counts. A
    comp / daily_free / free charge moved no credits (delta=0), so this no-ops.
    Idempotent via the `:refund` ref_id suffix against the partial unique index.
    Returns True if credits were returned, False on no-op. Never raises.
    """
    allowance_back = int((charge_result or {}).get("allowance_spent") or 0)
    purchased_back = int((charge_result or {}).get("purchased_spent") or 0)
    if allowance_back <= 0 and purchased_back <= 0:
        return False

    refund_ref = f"{ref_id}:refund"
    ctx_json = json.dumps(context) if context else None
    db = SessionLocal()
    try:
        u = (
            db.query(User)
            .filter(User.id == user_id)
            .with_for_update()
            .first()
        )
        if not u:
            return False
        if allowance_back > 0:
            u.allowance_credits = (u.allowance_credits or 0) + allowance_back
            db.add(CreditLedger(
                user_id=user_id, delta=allowance_back, bucket="allowance",
                reason="song_refund", ref_type="submitted_song",
                ref_id=refund_ref, context_json=ctx_json,
            ))
        if purchased_back > 0:
            u.purchased_credits = (u.purchased_credits or 0) + purchased_back
            db.add(CreditLedger(
                user_id=user_id, delta=purchased_back, bucket="purchased",
                reason="song_refund", ref_type="submitted_song",
                ref_id=refund_ref, context_json=ctx_json,
            ))
        try:
            db.commit()
        except IntegrityError:
            # Already refunded this charge (replay) -- the unique index collides.
            db.rollback()
            logger.info("refund_song replay ignored: ref_id=%s", refund_ref)
            return False
        return True
    except Exception:
        db.rollback()
        logger.exception("refund_song failed user=%s ref_id=%s", user_id, ref_id)
        return False
    finally:
        db.close()


# --- Grants (Stripe webhook + signup) ------------------------------------

def grant_credits(
    user_id: int,
    amount: int,
    *,
    bucket: str,
    reason: str,
    ref_type: Optional[str] = None,
    ref_id: Optional[str] = None,
    context: Optional[dict] = None,
    reset_allowance: bool = False,
) -> bool:
    """Idempotently grant `amount` credits to `bucket` on `user_id`.

    Idempotency is enforced by the credit_ledger partial unique index
    (reason, ref_id, bucket) -- a replayed Stripe event with the same
    event id will hit IntegrityError, we roll back, and return False.

    reset_allowance=True is the monthly-grant case: zero the allowance
    bucket before granting so an unspent allowance from the prior period
    doesn't compound on the new period's grant.

    Returns True on a fresh grant, False if it was a replay (ignored).
    """
    if bucket not in ("allowance", "purchased"):
        raise ValueError(f"Unknown bucket: {bucket!r}")
    if amount < 0:
        raise ValueError("grant_credits amount must be >= 0; use charge_credits for spend")

    db = SessionLocal()
    try:
        u = (
            db.query(User)
            .filter(User.id == user_id)
            .with_for_update()
            .first()
        )
        if not u:
            raise ValueError(f"User {user_id} not found")

        if reset_allowance:
            prior = u.allowance_credits or 0
            if prior > 0:
                # Record the expiry of the prior period's unspent allowance as
                # a negative ledger row so SUM(credit_ledger.delta) keeps
                # tracking the row balance (the documented source-of-truth
                # invariant). The ':expire' ref_id suffix keeps it
                # independently idempotent; on a webhook replay the +amount
                # grant row collides first and the whole txn rolls back, so
                # this row never double-applies.
                db.add(CreditLedger(
                    user_id=user_id, delta=-prior, bucket="allowance",
                    reason="allowance_expire", ref_type=ref_type,
                    ref_id=(f"{ref_id}:expire" if ref_id else None),
                    context_json=json.dumps({"expired": prior, "for_reason": reason}),
                ))
            u.allowance_credits = 0

        if amount > 0:
            if bucket == "allowance":
                u.allowance_credits = (u.allowance_credits or 0) + amount
            else:
                u.purchased_credits = (u.purchased_credits or 0) + amount

        ctx_json = json.dumps(context) if context else None
        db.add(CreditLedger(
            user_id=user_id, delta=amount, bucket=bucket,
            reason=reason, ref_type=ref_type, ref_id=ref_id, context_json=ctx_json,
        ))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        logger.info(
            "grant_credits replay ignored: reason=%s ref_id=%s bucket=%s",
            reason, ref_id, bucket,
        )
        return False
    finally:
        db.close()


# --- Admin a-la-carte adjustment -----------------------------------------

def admin_adjust_credits(
    user_id: int,
    amount: int,
    *,
    ref_id: str,
    note: Optional[str] = None,
    actor: Optional[str] = None,
) -> dict:
    """Admin grant (+) or deduct (-) on the PURCHASED bucket.

    amount > 0: grant `amount` permanent credits (reason='admin_grant').
    amount < 0: deduct up to `|amount|`, purchased first then allowance,
                clamped so neither bucket goes negative (reason='admin_deduct').

    ref_id must be unique per click (the caller passes a uuid) so repeated
    grants don't collide on the (reason, ref_id, bucket) idempotency index.
    Signed ledger rows keep the balance == ledger-sum invariant intact.

    Returns {granted, deducted, new_balance}.
    """
    if amount == 0:
        raise ValueError("admin_adjust_credits amount must be non-zero")

    ctx = {"note": note, "actor": actor}

    if amount > 0:
        grant_credits(
            user_id, amount, bucket="purchased", reason="admin_grant",
            ref_type="admin", ref_id=ref_id, context=ctx,
        )
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.id == user_id).first()
            bal = total_credits(u) if u else 0
        finally:
            db.close()
        return {"granted": amount, "deducted": 0, "new_balance": bal}

    # Deduct: purchased first, then allowance, never below zero.
    want = -amount
    db = SessionLocal()
    try:
        u = (
            db.query(User).filter(User.id == user_id).with_for_update().first()
        )
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        purchased = u.purchased_credits or 0
        allowance = u.allowance_credits or 0
        from_purchased = min(purchased, want)
        from_allowance = min(allowance, want - from_purchased)
        actually = from_purchased + from_allowance

        u.purchased_credits = purchased - from_purchased
        u.allowance_credits = allowance - from_allowance
        if from_purchased > 0:
            db.add(CreditLedger(
                user_id=user_id, delta=-from_purchased, bucket="purchased",
                reason="admin_deduct", ref_type="admin", ref_id=f"{ref_id}:purchased",
                context_json=json.dumps(ctx),
            ))
        if from_allowance > 0:
            db.add(CreditLedger(
                user_id=user_id, delta=-from_allowance, bucket="allowance",
                reason="admin_deduct", ref_type="admin", ref_id=f"{ref_id}:allowance",
                context_json=json.dumps(ctx),
            ))
        db.commit()
        return {
            "granted": 0, "deducted": actually,
            "new_balance": (u.allowance_credits or 0) + (u.purchased_credits or 0),
        }
    except IntegrityError:
        db.rollback()
        logger.info("admin_adjust_credits deduct replay ignored: ref_id=%s", ref_id)
        return {"granted": 0, "deducted": 0, "new_balance": None, "replayed": True}
    finally:
        db.close()


def set_comp_unlimited(
    user_id: int,
    unlimited: bool,
    *,
    note: Optional[str] = None,
    actor: Optional[str] = None,
) -> bool:
    """Toggle the admin unlimited-Charger comp on a user. Writes a delta=0
    'comp' audit row (reason 'comp_grant'/'comp_revoke') so the change shows in
    the ledger + activity timeline. Returns the new flag value."""
    db = SessionLocal()
    try:
        u = (
            db.query(User).filter(User.id == user_id).with_for_update().first()
        )
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        u.comp_unlimited = bool(unlimited)
        db.add(CreditLedger(
            user_id=user_id, delta=0, bucket=COMP_BUCKET,
            reason=("comp_grant" if unlimited else "comp_revoke"),
            ref_type="admin", ref_id=None,
            context_json=json.dumps({"note": note, "actor": actor}),
        ))
        db.commit()
        return bool(u.comp_unlimited)
    finally:
        db.close()


# --- Holds (Album Charger: estimate -> hold -> settle) -------------------
