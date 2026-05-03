"""Outbound Claude API usage log — backs the Claude Usage admin tab.

One row per Anthropic messages.create() call from the backend. Captures token
counts, computed USD cost, model, call site (which feature triggered the call),
and a context blob (song title/artist/draft id/etc) so the admin can see what
was being calibrated/recalibrated/etc when the spend happened.

Distinct from api_call_log (which logs INBOUND requests to the RC API).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS claude_api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            call_site VARCHAR(64) NOT NULL,
            model VARCHAR(64) NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            input_cost_usd REAL NOT NULL DEFAULT 0,
            output_cost_usd REAL NOT NULL DEFAULT 0,
            cache_creation_cost_usd REAL NOT NULL DEFAULT 0,
            cache_read_cost_usd REAL NOT NULL DEFAULT 0,
            total_cost_usd REAL NOT NULL DEFAULT 0,
            duration_ms INTEGER,
            stop_reason VARCHAR(32),
            ok INTEGER NOT NULL DEFAULT 1,
            error TEXT,
            pricing_source VARCHAR(32),
            context_json TEXT
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_claude_usage_ts ON claude_api_usage(ts DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_claude_usage_site_ts ON claude_api_usage(call_site, ts DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_claude_usage_model_ts ON claude_api_usage(model, ts DESC)"
    ))
