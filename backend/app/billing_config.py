"""Static billing config: tiers, packs, per-action credit costs.

Two entitlement types over one metering core:
  - Subscription tier (allowance bucket, resets monthly on invoice.paid):
    buys zero-marginal-cost access (Library pagination, Compass readings,
    cache-hit reads).
  - Credit packs (purchased bucket, permanent): buy variable-cost compute
    (Charger engine runs, i.e. fresh Opus calibrations).

Spend order on any charge: allowance first, then purchased.

Costs are denominated in credits where 1 credit ~= 1,000 words / ~$0.04 of
Opus spend (peg derived from claude_api_usage). Pricing here is placeholder
until RISING-COMPASS-FINANCIALS lands; the dataclasses + maps stay stable,
the numbers float.

Stripe price IDs are read at call time from settings, so a missing env var
returns None rather than blowing up at import.
"""

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class Tier:
    key: str
    label: str
    monthly_allowance: int          # credits granted at subscription period start
    stripe_price_attr: str          # which settings attr holds the Stripe price ID

    @property
    def stripe_price_id(self):
        if not self.stripe_price_attr:
            return None
        return getattr(settings, self.stripe_price_attr, None) or None


# Subscription tiers. 'free' has no Stripe product.
TIER_FREE = Tier(key="free", label="Free", monthly_allowance=0,  stripe_price_attr="")
TIER_PLUS = Tier(key="plus", label="Plus", monthly_allowance=15, stripe_price_attr="stripe_price_plus")
TIER_PRO  = Tier(key="pro",  label="Pro",  monthly_allowance=60, stripe_price_attr="stripe_price_pro")

TIERS: dict[str, Tier] = {t.key: t for t in (TIER_FREE, TIER_PLUS, TIER_PRO)}

# Tier sets the rest of the codebase checks against.
# PAID_USER_TIERS is for the consumer-side subscription_tier.
# PAID_API_TIERS is for the B2B api_clients.plan_tier. 'system' is NOT in
# this set on purpose: it's the seed default for legacy-public, which is
# the key the *anonymous* RC frontend uses -- so 'system' is a
# tier-orthogonal identity marker, not a paid grant. Paid B2B today is
# explicit plus/pro (paying customers) or 'internal' (first-party clients
# like chadlewine). The api_clients.behavior=='service' path is also
# treated as paid for entitlement purposes -- see is_paid_api_client.
PAID_USER_TIERS: frozenset = frozenset({"plus", "pro"})
PAID_API_TIERS:  frozenset = frozenset({"plus", "pro", "internal"})


@dataclass(frozen=True)
class Pack:
    key: str
    label: str
    credits: int
    stripe_price_attr: str

    @property
    def stripe_price_id(self):
        return getattr(settings, self.stripe_price_attr, None) or None


# Credit packs (one-time purchases). Grant goes to 'purchased' bucket.
PACK_25  = Pack(key="pack_25",  label="25 credits",  credits=25,  stripe_price_attr="stripe_price_pack_25")
PACK_100 = Pack(key="pack_100", label="100 credits", credits=100, stripe_price_attr="stripe_price_pack_100")
PACK_300 = Pack(key="pack_300", label="300 credits", credits=300, stripe_price_attr="stripe_price_pack_300")

PACKS: dict[str, Pack] = {p.key: p for p in (PACK_25, PACK_100, PACK_300)}


# Per-action credit costs.
#
# Cache hit is FREE for signed-in users -- reading a previously-calibrated
# song page does not run Opus, so it is the subscription benefit (no
# variable cost). Only the engine miss (a fresh calibration) costs credits.
#
# (COST_ALBUM_TRACK_MISS retired 2026-08-21 with the Album Charger. It sized
# the per-track hold that settle_hold reconciled, and nothing else ever used
# either of them.)
COST_SONG_MISS         = 1
COST_SONG_CACHE_HIT    = 0

# Anonymous (no Clerk session) Charger daily allowance per-IP. Cost
# firewall + funnel; signed-in users are gated by credits instead.
ANON_CHARGER_DAILY_LIMIT = 3

# Free-tier daily charge allotment (code default; admin-tunable via
# system_flags -> lyrical_charger.free_daily_charges). A FREE-TIER signed-in
# user gets this many fresh single-song Charger runs per UTC day at no credit
# cost, consumed BEFORE any credit bucket. Tracked in credit_ledger as
# delta=0 'daily_free' rows (no balance impact), so "used today" is a count of
# those rows since UTC midnight -- no reset job, full audit trail. Paid tiers
# (plus/pro) get NO daily-free allotment: they spend allowance -> purchased as
# usual (they pay for volume). Album charging is unaffected (hold/settle model).
DAILY_FREE_CHARGES = 5


def is_paid_user(subscription_tier) -> bool:
    """True if a user's subscription tier counts as paid (plus or pro)."""
    return (subscription_tier or "free") in PAID_USER_TIERS


def is_paid_api_client(plan_tier, behavior=None) -> bool:
    """True if a B2B api_clients entry counts as paid for entitlement
    purposes. Two paths:
      - plan_tier in {plus, pro, internal}: explicit paid tier
      - behavior == 'service': first-party service callers (chadlewine
        batch jobs, internal scripts) get paid-grade access regardless
        of plan_tier, because they are not the anonymous public surface.

    The legacy-public client has plan_tier='system' + behavior='public';
    that combination is intentionally NOT paid -- it's the anon/free
    surface even though it's seeded by the system.
    """
    if (plan_tier or "free") in PAID_API_TIERS:
        return True
    if behavior == "service":
        return True
    return False
