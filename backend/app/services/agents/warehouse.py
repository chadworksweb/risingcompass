"""In-house agent mini-warehouse.

The external LEIT Agent Warehouse (and Mickey) were decommissioned, so RC's own
autonomous agents get a home INSIDE RC admin instead: identity + health + run
history + cost, separate from whatever each agent FINDS. Dusty (the daily
clutter sweep) is the first resident.

This module owns:
  - the agent REGISTRY (static identity/metadata),
  - run-tracking helpers (`start_run` / `finish_run`) the agents call,
  - health + metrics derivation the admin page reads.

Health is derived from `agent_runs` recency + status (these are cron agents, not
long-running daemons -- there is no PM2 process or heartbeat to poll). Cost is
derived from `claude_api_usage` by the agent's `call_site`. Everything is
env-scoped (local dev shares the prod DB via the tunnel)."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import func

from app.config import settings
from app.database import SessionLocal
from app.models import AgentRun, ClaudeApiUsage

logger = logging.getLogger(__name__)


# --- Registry: static identity for each in-house agent ---------------------
# Keyed by agent_id. `call_site` ties the agent to its Claude spend in
# claude_api_usage; `overdue_hours` is the health window (no successful run
# within it -> "overdue").
AGENTS = {
    "custodian-001": {
        "id": "custodian-001",
        "codename": "Custodian 001",
        "nickname": "Dusty",
        "venture": "Rising Compass",
        "layer": 1,
        "role": "Library clutter sweep",
        "what": (
            "Daily sweep of public Lyrical Charger additions for clutter -- "
            "gibberish, unknown non-artists, and content that belongs on the "
            "Creative/Curio Charger. Flags findings to the audit queue for human "
            "review; never changes the live site itself."
        ),
        "schedule": "Daily ~16:30 UTC (after the reading/iTunes lane)",
        "model": settings.agent_model,
        "call_site": "leit_sweep",
        "findings_url": "/api/admin/dashboard/clutter",
        "overdue_hours": 36,
    },
}


def get_agent(agent_id: str) -> dict | None:
    return AGENTS.get(agent_id)


# --- Run tracking (called by the agents) -----------------------------------

def start_run(agent_id: str, trigger: str = "cron") -> int | None:
    """Open an agent_runs row (status='running'). Returns its id, or None on a
    swallowed error -- run tracking must never break the agent it tracks."""
    try:
        db = SessionLocal()
        try:
            row = AgentRun(
                agent_id=agent_id,
                trigger=(trigger if trigger in ("cron", "admin") else "cron"),
                status="running",
                environment=settings.environment,
                started_at=datetime.utcnow(),
            )
            db.add(row)
            db.commit()
            return row.id
        finally:
            db.close()
    except Exception:
        logger.exception("agent start_run failed (agent=%s)", agent_id)
        return None


def finish_run(run_id: int | None, *, status: str, scanned: int = 0,
               flagged: int = 0, error: str | None = None) -> None:
    """Close an agent_runs row with the outcome. Fail-soft."""
    if run_id is None:
        return
    try:
        db = SessionLocal()
        try:
            row = db.query(AgentRun).filter(AgentRun.id == run_id).first()
            if not row:
                return
            row.status = status
            row.scanned = scanned or 0
            row.flagged = flagged or 0
            row.error = (error or None)
            row.finished_at = datetime.utcnow()
            if row.started_at:
                row.duration_ms = int(
                    (row.finished_at - row.started_at).total_seconds() * 1000
                )
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("agent finish_run failed (run_id=%s)", run_id)


# --- Health + metrics (read by the admin page) -----------------------------

def _cost_for(db, call_site: str) -> dict:
    """All-time + trailing-30-day Claude spend for an agent's call_site."""
    def _sum(since=None):
        q = db.query(
            func.coalesce(func.sum(ClaudeApiUsage.total_cost_usd), 0.0),
            func.count(ClaudeApiUsage.id),
        ).filter(ClaudeApiUsage.call_site == call_site)
        if since is not None:
            q = q.filter(ClaudeApiUsage.ts >= since)
        cost, calls = q.one()
        return round(float(cost or 0.0), 4), int(calls or 0)

    all_cost, all_calls = _sum()
    since_30 = datetime.utcnow() - timedelta(days=30)
    cost_30, calls_30 = _sum(since_30)
    return {
        "cost_all_usd": all_cost, "calls_all": all_calls,
        "cost_30d_usd": cost_30, "calls_30d": calls_30,
    }


def _health(agent: dict, last_run: AgentRun | None) -> dict:
    """Derive a health badge from the agent's most recent run."""
    now = datetime.utcnow()
    if last_run is None:
        return {"status": "never_run", "label": "Never run", "last_run_at": None}
    age_h = (now - last_run.started_at).total_seconds() / 3600 if last_run.started_at else None
    base = {"last_run_at": last_run.started_at.isoformat() if last_run.started_at else None,
            "last_status": last_run.status, "age_hours": round(age_h, 1) if age_h is not None else None}
    if last_run.status == "running":
        # A run that never closed (crash mid-run) -> stalled after a grace window.
        stalled = age_h is not None and age_h > 2
        base.update(status=("stalled" if stalled else "running"),
                    label=("Stalled" if stalled else "Running"))
    elif last_run.status == "error":
        base.update(status="error", label="Last run errored")
    elif age_h is not None and age_h > agent.get("overdue_hours", 36):
        base.update(status="overdue", label="Overdue")
    else:
        base.update(status="healthy", label="Healthy")
    return base


def _metrics(db, agent_id: str, environment: str) -> dict:
    base = db.query(AgentRun).filter(
        AgentRun.agent_id == agent_id, AgentRun.environment == environment
    )
    total = base.count()
    ok = base.filter(AgentRun.status == "ok").count()
    errored = base.filter(AgentRun.status == "error").count()
    sums = db.query(
        func.coalesce(func.sum(AgentRun.scanned), 0),
        func.coalesce(func.sum(AgentRun.flagged), 0),
    ).filter(AgentRun.agent_id == agent_id, AgentRun.environment == environment).one()
    return {
        "runs_total": total,
        "runs_ok": ok,
        "runs_error": errored,
        "success_rate": (round(ok / total * 100) if total else None),
        "scanned_total": int(sums[0] or 0),
        "flagged_total": int(sums[1] or 0),
    }


def agent_summary(agent_id: str, environment: str) -> dict | None:
    agent = AGENTS.get(agent_id)
    if not agent:
        return None
    db = SessionLocal()
    try:
        last_run = (
            db.query(AgentRun)
            .filter(AgentRun.agent_id == agent_id, AgentRun.environment == environment)
            .order_by(AgentRun.started_at.desc())
            .first()
        )
        return {
            **{k: agent[k] for k in ("id", "codename", "nickname", "venture",
                                     "layer", "role", "what", "schedule", "model",
                                     "findings_url")},
            "health": _health(agent, last_run),
            "metrics": _metrics(db, agent_id, environment),
            "cost": _cost_for(db, agent["call_site"]),
        }
    finally:
        db.close()


def list_agents(environment: str) -> list[dict]:
    return [agent_summary(aid, environment) for aid in AGENTS]


def list_runs(agent_id: str, environment: str, limit: int = 50) -> list[dict]:
    limit = max(1, min(limit, 200))
    db = SessionLocal()
    try:
        rows = (
            db.query(AgentRun)
            .filter(AgentRun.agent_id == agent_id, AgentRun.environment == environment)
            .order_by(AgentRun.started_at.desc())
            .limit(limit)
            .all()
        )
        return [{
            "id": r.id,
            "trigger": r.trigger,
            "status": r.status,
            "scanned": r.scanned,
            "flagged": r.flagged,
            "error": r.error,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_ms": r.duration_ms,
        } for r in rows]
    finally:
        db.close()
