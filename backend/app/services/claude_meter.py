"""Outbound Claude API metering — wraps Anthropic messages.create() and logs
token counts, cost, and request context to the claude_api_usage table.

Two entry points:

  tracked_create(client, *, call_site, context, **kwargs) -> response
  await tracked_create_async(client, *, call_site, context, **kwargs) -> response

Behavior is otherwise identical to client.messages.create(...) — exceptions
propagate, the response is returned untouched. Failed calls are still logged
(ok=0, error=...) so prompt-explosion bugs and Anthropic outages show up in
the admin tab as cost-zero rows.

The usage row is written inline via a short-lived ORM session. The old
background queue + direct-Turso connection existed only to dodge the embedded
replica's WAL contention; Postgres has none, and each metered call already
took seconds against the Anthropic API, so a fast local INSERT after it is
negligible. Write errors are swallowed — usage logs are telemetry, not a
correctness signal. See RISING-COMPASS-POSTGRES-MIGRATION.md.
"""

import json
import logging
import time as _time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.database import SessionLocal
from app.models import ClaudeApiUsage

logger = logging.getLogger(__name__)


# --- Pricing ---------------------------------------------------------------
# USD per million tokens, by model family. Anthropic's published list pricing
# (https://www.anthropic.com/pricing) — keep in sync when new tiers ship.
#
# pricing_source on each row records which entry was used. "fallback_opus"
# means the model wasn't recognized and we charged Opus rates; treat those
# rows as approximate.

@dataclass(frozen=True)
class Rates:
    input_per_mtok: float
    output_per_mtok: float
    cache_creation_per_mtok: float  # 5m write
    cache_read_per_mtok: float


_OPUS_RATES = Rates(15.0, 75.0, 18.75, 1.50)
_SONNET_RATES = Rates(3.0, 15.0, 3.75, 0.30)
_HAIKU_RATES = Rates(1.0, 5.0, 1.25, 0.10)


def _resolve_rates(model: str) -> tuple[Rates, str]:
    """Return (rates, pricing_source) for a given model id.

    Matches by family substring so new minor versions (4-7, 4-8, ...) inherit
    the family rate without a code change. Unknown models fall back to Opus
    pricing (the conservative upper bound) with a flagged source.
    """
    m = (model or "").lower()
    if "opus" in m:
        return _OPUS_RATES, "opus"
    if "sonnet" in m:
        return _SONNET_RATES, "sonnet"
    if "haiku" in m:
        return _HAIKU_RATES, "haiku"
    return _OPUS_RATES, "fallback_opus"


def _compute_costs(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
) -> tuple[float, float, float, float, float, str]:
    rates, source = _resolve_rates(model)
    input_cost = input_tokens / 1_000_000 * rates.input_per_mtok
    output_cost = output_tokens / 1_000_000 * rates.output_per_mtok
    cache_creation_cost = cache_creation_tokens / 1_000_000 * rates.cache_creation_per_mtok
    cache_read_cost = cache_read_tokens / 1_000_000 * rates.cache_read_per_mtok
    total = input_cost + output_cost + cache_creation_cost + cache_read_cost
    return input_cost, output_cost, cache_creation_cost, cache_read_cost, total, source


# --- Writer ----------------------------------------------------------------

def _write_usage(row: dict) -> None:
    """Insert one claude_api_usage row via a short-lived ORM session. Swallows
    errors — usage logging must never mask the metered call's outcome."""
    try:
        db = SessionLocal()
        try:
            db.add(ClaudeApiUsage(**row))
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("claude_api_usage write failed for %s", row.get("call_site"))


# --- Token extraction + row build -----------------------------------------

def _extract_usage(response: Any) -> tuple[int, int, int, int, str | None]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0, 0, 0, getattr(response, "stop_reason", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_creation = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    stop_reason = getattr(response, "stop_reason", None)
    return input_tokens, output_tokens, cache_creation, cache_read, stop_reason


def _build_row(
    *,
    call_site: str,
    model: str,
    context: dict | None,
    duration_ms: int,
    input_tokens: int,
    output_tokens: int,
    cache_creation: int,
    cache_read: int,
    stop_reason: str | None,
    ok: bool,
    error: str | None,
) -> dict:
    in_cost, out_cost, cc_cost, cr_cost, total, src = _compute_costs(
        model, input_tokens, output_tokens, cache_creation, cache_read,
    )
    ctx_json = None
    if context:
        try:
            # Cap individual values so a runaway lyrics blob doesn't bloat the log.
            trimmed = {k: (v[:200] if isinstance(v, str) else v) for k, v in context.items()}
            ctx_json = json.dumps(trimmed, default=str)
        except Exception:
            ctx_json = None
    return {
        "ts": datetime.utcnow(),
        "call_site": call_site[:64],
        "model": (model or "unknown")[:64],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_tokens": cache_creation,
        "cache_read_tokens": cache_read,
        "input_cost_usd": round(in_cost, 8),
        "output_cost_usd": round(out_cost, 8),
        "cache_creation_cost_usd": round(cc_cost, 8),
        "cache_read_cost_usd": round(cr_cost, 8),
        "total_cost_usd": round(total, 8),
        "duration_ms": duration_ms,
        "stop_reason": stop_reason,
        "ok": 1 if ok else 0,
        "error": (error[:500] if error else None),
        "pricing_source": src,
        "context_json": ctx_json,
    }


# --- Public wrappers -------------------------------------------------------

def tracked_create(client, *, call_site: str, context: dict | None = None, **kwargs):
    """Sync wrapper around client.messages.create. Logs usage + cost.

    `client` is an Anthropic instance, `**kwargs` flow straight through to
    messages.create (model, max_tokens, system, messages, etc).
    """
    model = kwargs.get("model", "")
    start = _time.time()
    try:
        response = client.messages.create(**kwargs)
    except Exception as exc:
        duration_ms = int((_time.time() - start) * 1000)
        _write_usage(_build_row(
            call_site=call_site, model=model, context=context,
            duration_ms=duration_ms,
            input_tokens=0, output_tokens=0, cache_creation=0, cache_read=0,
            stop_reason=None, ok=False, error=f"{type(exc).__name__}: {exc}",
        ))
        raise
    duration_ms = int((_time.time() - start) * 1000)
    in_t, out_t, cc_t, cr_t, stop = _extract_usage(response)
    _write_usage(_build_row(
        call_site=call_site, model=model, context=context,
        duration_ms=duration_ms,
        input_tokens=in_t, output_tokens=out_t,
        cache_creation=cc_t, cache_read=cr_t,
        stop_reason=stop, ok=True, error=None,
    ))
    return response


async def tracked_create_async(client, *, call_site: str, context: dict | None = None, **kwargs):
    """Async wrapper around client.messages.create. Logs usage + cost."""
    model = kwargs.get("model", "")
    start = _time.time()
    try:
        response = await client.messages.create(**kwargs)
    except Exception as exc:
        duration_ms = int((_time.time() - start) * 1000)
        _write_usage(_build_row(
            call_site=call_site, model=model, context=context,
            duration_ms=duration_ms,
            input_tokens=0, output_tokens=0, cache_creation=0, cache_read=0,
            stop_reason=None, ok=False, error=f"{type(exc).__name__}: {exc}",
        ))
        raise
    duration_ms = int((_time.time() - start) * 1000)
    in_t, out_t, cc_t, cr_t, stop = _extract_usage(response)
    _write_usage(_build_row(
        call_site=call_site, model=model, context=context,
        duration_ms=duration_ms,
        input_tokens=in_t, output_tokens=out_t,
        cache_creation=cc_t, cache_read=cr_t,
        stop_reason=stop, ok=True, error=None,
    ))
    return response
