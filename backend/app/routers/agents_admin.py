"""In-house agent mini-warehouse -- admin API.

Backs Site Admin -> System -> Agents: identity + health + run history + cost for
RC's own autonomous agents (Dusty the clutter sweep is the first resident). This
is the agent's OWN home -- what it IS and what it has DONE -- kept separate from
what it FOUND (the clutter audit queue). The external LEIT Agent Warehouse was
decommissioned; this is the replacement, scoped to RC.

Env-filtered by default (`environment='prod'`): local dev shares the prod DB via
the tunnel, so the page passes `?environment=local` when testing locally.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.routers.admin import verify_admin_key
from app.services.agents import warehouse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/agents", tags=["agents-admin"])


@router.get("", dependencies=[Depends(verify_admin_key)])
def list_agents(environment: str = "prod"):
    """All in-house agents with identity + health + metrics + cost."""
    return {"agents": warehouse.list_agents(environment), "environment": environment}


@router.get("/{agent_id}", dependencies=[Depends(verify_admin_key)])
def agent_detail(agent_id: str, environment: str = "prod"):
    summary = warehouse.agent_summary(agent_id, environment)
    if not summary:
        raise HTTPException(404, "unknown agent")
    return summary


@router.get("/{agent_id}/runs", dependencies=[Depends(verify_admin_key)])
def agent_runs(agent_id: str, environment: str = "prod", limit: int = 50):
    if warehouse.get_agent(agent_id) is None:
        raise HTTPException(404, "unknown agent")
    return {"runs": warehouse.list_runs(agent_id, environment, limit)}
