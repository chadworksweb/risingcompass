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
from datetime import datetime, timedelta
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


# --- Free-tier daily allotment -------------------------------------------

def _utc_day_start() -> datetime:
    """UTC midnight of the current day -- the daily-free reset boundary."""
    return datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


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


# --- Holds (Album Charger: estimate -> hold -> settle) -------------------

def hold_credits(
    user_id: int,
    cost: int,
    *,
    ref_type: str,
    ref_id: str,
    context: Optional[dict] = None,
) -> dict:
    """Reserve worst-case credits for an async job. Same accounting as
    charge_credits but reason='hold' so settle_hold can find the original
    bucket split when reconciling.
    """
    return charge_credits(
        user_id, cost,
        reason="hold", ref_type=ref_type, ref_id=ref_id, context=context,
    )


def settle_hold(
    user_id: int,
    *,
    hold_cost: int,
    actual_cost: int,
    ref_type: str,
    ref_id: str,
    context: Optional[dict] = None,
) -> dict:
    """Reconcile a hold against the actual cost.

    actual_cost <  hold_cost  -> refund the difference to the bucket(s) the
                                 hold was taken from, in the same proportion
    actual_cost == hold_cost  -> write a no-delta settlement marker
    actual_cost >  hold_cost  -> charge the extra (raises 402 if balance is
                                 insufficient -- rare, since holds are
                                 sized pessimistic)

    The settlement row(s) carry reason='settle' with a ref_id suffix per
    write (":refund:allowance", ":refund:purchased", ":extra") so the partial
    unique index allows the multi-row settlement without a collision while
    still keeping each row individually idempotent against replays.
    """
    if actual_cost == hold_cost:
        db = SessionLocal()
        try:
            db.add(CreditLedger(
                user_id=user_id, delta=0, bucket="settlement",
                reason="settle", ref_type=ref_type, ref_id=ref_id,
                context_json=json.dumps({
                    "hold_cost": hold_cost, "actual_cost": actual_cost,
                    **(context or {}),
                }),
            ))
            db.commit()
        except IntegrityError:
            db.rollback()
        finally:
            db.close()
        return {"refund": 0, "extra_charge": 0}

    if actual_cost < hold_cost:
        refund = hold_cost - actual_cost
        db = SessionLocal()
        try:
            # Read the hold's original bucket split so the refund mirrors it.
            holds = (
                db.query(CreditLedger)
                .filter(CreditLedger.reason == "hold")
                .filter(CreditLedger.ref_type == ref_type)
                .filter(CreditLedger.ref_id == ref_id)
                .all()
            )
            from_allowance = sum(-h.delta for h in holds if h.bucket == "allowance")
            from_purchased = sum(-h.delta for h in holds if h.bucket == "purchased")
            total_held = from_allowance + from_purchased
            if total_held <= 0:
                # No hold rows found -- nothing to refund. Still write a
                # marker so the admin view shows the attempt.
                db.add(CreditLedger(
                    user_id=user_id, delta=0, bucket="settlement",
                    reason="settle", ref_type=ref_type, ref_id=ref_id,
                    context_json=json.dumps({
                        "hold_cost": hold_cost, "actual_cost": actual_cost,
                        "note": "no hold rows found",
                        **(context or {}),
                    }),
                ))
                db.commit()
                return {"refund": 0, "extra_charge": 0}

            # Proportional refund so the bucket math stays balanced.
            refund_allowance = round(refund * from_allowance / total_held)
            refund_purchased = refund - refund_allowance

            u = (
                db.query(User)
                .filter(User.id == user_id)
                .with_for_update()
                .first()
            )
            if not u:
                raise ValueError(f"User {user_id} not found")
            u.allowance_credits = (u.allowance_credits or 0) + refund_allowance
            u.purchased_credits = (u.purchased_credits or 0) + refund_purchased

            ctx_base = {
                "hold_cost": hold_cost, "actual_cost": actual_cost,
                "kind": "refund", **(context or {}),
            }
            if refund_allowance > 0:
                db.add(CreditLedger(
                    user_id=user_id, delta=refund_allowance, bucket="allowance",
                    reason="settle", ref_type=ref_type,
                    ref_id=f"{ref_id}:refund:allowance",
                    context_json=json.dumps(ctx_base),
                ))
            if refund_purchased > 0:
                db.add(CreditLedger(
                    user_id=user_id, delta=refund_purchased, bucket="purchased",
                    reason="settle", ref_type=ref_type,
                    ref_id=f"{ref_id}:refund:purchased",
                    context_json=json.dumps(ctx_base),
                ))
            db.commit()
            return {"refund": refund, "extra_charge": 0}
        except IntegrityError:
            db.rollback()
            logger.info("settle_hold refund replay ignored: ref_id=%s", ref_id)
            return {"refund": 0, "extra_charge": 0, "replayed": True}
        finally:
            db.close()

    # actual_cost > hold_cost -- charge the extra.
    extra = actual_cost - hold_cost
    charge_credits(
        user_id, extra,
        reason="settle", ref_type=ref_type, ref_id=f"{ref_id}:extra",
        context={
            "hold_cost": hold_cost, "actual_cost": actual_cost,
            "kind": "extra_charge", **(context or {}),
        },
    )
    return {"refund": 0, "extra_charge": extra}
