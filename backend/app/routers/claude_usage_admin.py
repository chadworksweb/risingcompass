"""Admin endpoints for outbound Claude API usage — totals, breakdowns, calls.

Backs the "Claude Usage" admin tab. Reads from claude_api_usage, populated by
app.services.claude_meter on every Anthropic messages.create() call.
"""

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, desc, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ClaudeApiUsage
from app.routers.admin import verify_admin_key

router = APIRouter(prefix="/api/admin/claude-usage", tags=["claude-usage-admin"])


def _resolve_since(days: int) -> datetime:
    days = max(1, min(days, 365))
    return datetime.utcnow() - timedelta(days=days)


@router.get("/summary", dependencies=[Depends(verify_admin_key)])
def usage_summary(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    """Aggregate spend over the trailing N days.

    Returns:
      - totals: calls, tokens, total_cost_usd
      - by_call_site: ordered by total_cost_usd desc
      - by_model: ordered by total_cost_usd desc
      - daily: total_cost_usd + calls per UTC day, ascending by date
      - all_time_cost_usd: sum across the full table (cheap; one row count)
    """
    since = _resolve_since(days)

    totals_row = (
        db.query(
            func.count(ClaudeApiUsage.id).label("calls"),
            func.coalesce(func.sum(ClaudeApiUsage.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(ClaudeApiUsage.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(ClaudeApiUsage.cache_creation_tokens), 0).label("cache_creation_tokens"),
            func.coalesce(func.sum(ClaudeApiUsage.cache_read_tokens), 0).label("cache_read_tokens"),
            func.coalesce(func.sum(ClaudeApiUsage.total_cost_usd), 0.0).label("total_cost_usd"),
            func.sum(case((ClaudeApiUsage.ok == 0, 1), else_=0)).label("error_calls"),
        )
        .filter(ClaudeApiUsage.ts >= since)
        .first()
    )

    by_site_rows = (
        db.query(
            ClaudeApiUsage.call_site,
            func.count(ClaudeApiUsage.id).label("calls"),
            func.coalesce(func.sum(ClaudeApiUsage.total_cost_usd), 0.0).label("cost_usd"),
            func.coalesce(func.sum(ClaudeApiUsage.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(ClaudeApiUsage.output_tokens), 0).label("output_tokens"),
            func.avg(ClaudeApiUsage.total_cost_usd).label("avg_cost_usd"),
        )
        .filter(ClaudeApiUsage.ts >= since)
        .group_by(ClaudeApiUsage.call_site)
        .order_by(desc("cost_usd"))
        .all()
    )

    by_model_rows = (
        db.query(
            ClaudeApiUsage.model,
            func.count(ClaudeApiUsage.id).label("calls"),
            func.coalesce(func.sum(ClaudeApiUsage.total_cost_usd), 0.0).label("cost_usd"),
        )
        .filter(ClaudeApiUsage.ts >= since)
        .group_by(ClaudeApiUsage.model)
        .order_by(desc("cost_usd"))
        .all()
    )

    daily_rows = (
        db.query(
            func.date(ClaudeApiUsage.ts).label("day"),
            func.count(ClaudeApiUsage.id).label("calls"),
            func.coalesce(func.sum(ClaudeApiUsage.total_cost_usd), 0.0).label("cost_usd"),
        )
        .filter(ClaudeApiUsage.ts >= since)
        .group_by("day")
        .order_by("day")
        .all()
    )

    daily_site_rows = (
        db.query(
            func.date(ClaudeApiUsage.ts).label("day"),
            ClaudeApiUsage.call_site,
            func.coalesce(func.sum(ClaudeApiUsage.total_cost_usd), 0.0).label("cost_usd"),
        )
        .filter(ClaudeApiUsage.ts >= since)
        .group_by("day", ClaudeApiUsage.call_site)
        .order_by("day")
        .all()
    )

    all_time_cost = (
        db.query(func.coalesce(func.sum(ClaudeApiUsage.total_cost_usd), 0.0)).scalar() or 0.0
    )
    all_time_calls = db.query(func.count(ClaudeApiUsage.id)).scalar() or 0

    return {
        "since": since.isoformat(),
        "days": days,
        "totals": {
            "calls": int(totals_row.calls or 0),
            "error_calls": int(totals_row.error_calls or 0),
            "input_tokens": int(totals_row.input_tokens or 0),
            "output_tokens": int(totals_row.output_tokens or 0),
            "cache_creation_tokens": int(totals_row.cache_creation_tokens or 0),
            "cache_read_tokens": int(totals_row.cache_read_tokens or 0),
            "total_cost_usd": round(float(totals_row.total_cost_usd or 0.0), 6),
        },
        "by_call_site": [
            {
                "call_site": r.call_site,
                "calls": int(r.calls),
                "cost_usd": round(float(r.cost_usd or 0.0), 6),
                "input_tokens": int(r.input_tokens or 0),
                "output_tokens": int(r.output_tokens or 0),
                "avg_cost_usd": round(float(r.avg_cost_usd or 0.0), 6),
            }
            for r in by_site_rows
        ],
        "by_model": [
            {
                "model": r.model,
                "calls": int(r.calls),
                "cost_usd": round(float(r.cost_usd or 0.0), 6),
            }
            for r in by_model_rows
        ],
        "daily": [
            {
                "day": r.day,
                "calls": int(r.calls),
                "cost_usd": round(float(r.cost_usd or 0.0), 6),
            }
            for r in daily_rows
        ],
        "daily_by_call_site": [
            {
                "day": r.day,
                "call_site": r.call_site,
                "cost_usd": round(float(r.cost_usd or 0.0), 6),
            }
            for r in daily_site_rows
        ],
        "all_time": {
            "calls": int(all_time_calls),
            "total_cost_usd": round(float(all_time_cost), 6),
        },
    }


@router.get("/calls", dependencies=[Depends(verify_admin_key)])
def list_calls(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    call_site: str | None = None,
    model: str | None = None,
    days: int = Query(30, ge=1, le=365),
    only_errors: bool = False,
    db: Session = Depends(get_db),
):
    """Paginated list of recent Claude API calls. Newest first."""
    since = _resolve_since(days)

    q = db.query(ClaudeApiUsage).filter(ClaudeApiUsage.ts >= since)
    if call_site:
        q = q.filter(ClaudeApiUsage.call_site == call_site)
    if model:
        q = q.filter(ClaudeApiUsage.model == model)
    if only_errors:
        q = q.filter(ClaudeApiUsage.ok == 0)

    total = q.count()
    rows = (
        q.order_by(desc(ClaudeApiUsage.ts))
        .offset(offset)
        .limit(limit)
        .all()
    )

    def _row(r: ClaudeApiUsage) -> dict:
        ctx = None
        if r.context_json:
            try:
                ctx = json.loads(r.context_json)
            except Exception:
                ctx = None
        return {
            "id": r.id,
            "ts": r.ts.isoformat() if r.ts else None,
            "call_site": r.call_site,
            "model": r.model,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "cache_creation_tokens": r.cache_creation_tokens,
            "cache_read_tokens": r.cache_read_tokens,
            "input_cost_usd": r.input_cost_usd,
            "output_cost_usd": r.output_cost_usd,
            "cache_creation_cost_usd": r.cache_creation_cost_usd,
            "cache_read_cost_usd": r.cache_read_cost_usd,
            "total_cost_usd": r.total_cost_usd,
            "duration_ms": r.duration_ms,
            "stop_reason": r.stop_reason,
            "ok": bool(r.ok),
            "error": r.error,
            "pricing_source": r.pricing_source,
            "context": ctx,
        }

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "calls": [_row(r) for r in rows],
    }
