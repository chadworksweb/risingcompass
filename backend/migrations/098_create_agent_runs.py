"""Create agent_runs -- the operational ledger behind the admin "Agents"
mini-warehouse (the external LEIT Agent Warehouse was decommissioned, so each
in-house RC agent gets a home inside RC admin instead).

One row per run of an RC agent (Dusty the clutter sweep is the first resident):
when it ran, by what trigger, success/failure, and how much it found -- separate
from what it FOUND (`clutter_audits`). Generic + agent_id-keyed so future agents
share the same table. Health on the page = last-run recency + status (these are
cron agents, not daemons); cost is derived from `claude_api_usage` by call_site.
Env-tagged like the other LEIT tables.

PG-compatible (063+). create_all() handles fresh installs; this is the
idempotent prod path.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS agent_runs (
            id SERIAL PRIMARY KEY,
            agent_id VARCHAR(40) NOT NULL,
            trigger VARCHAR(16) NOT NULL DEFAULT 'cron',
            status VARCHAR(16) NOT NULL DEFAULT 'running',
            scanned INTEGER NOT NULL DEFAULT 0,
            flagged INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            environment VARCHAR(10) NOT NULL DEFAULT 'local',
            started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP,
            duration_ms INTEGER
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_started "
        "ON agent_runs(agent_id, started_at)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_env "
        "ON agent_runs(environment)"
    ))
