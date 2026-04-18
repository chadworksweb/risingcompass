"""API client metering — accounts, keys, and per-call usage log.

Foundation for the "API Monitor" admin tab. Each company/service that calls
the RC API is an api_clients row; each row owns one or more api_client_keys.
Every /api/* request is logged to api_call_log with a resolved client_id.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS api_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug VARCHAR(64) UNIQUE NOT NULL,
            name TEXT NOT NULL,
            contact_email TEXT,
            plan_tier VARCHAR(32) DEFAULT 'trial',
            status VARCHAR(16) DEFAULT 'active',
            behavior VARCHAR(16) NOT NULL DEFAULT 'service',
            notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS api_client_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL REFERENCES api_clients(id) ON DELETE CASCADE,
            key_hash VARCHAR(64) NOT NULL UNIQUE,
            key_prefix VARCHAR(12) NOT NULL,
            label TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_used_at DATETIME,
            revoked_at DATETIME
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_client_keys(key_hash)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_api_keys_client ON api_client_keys(client_id)"))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS api_call_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER REFERENCES api_clients(id) ON DELETE SET NULL,
            ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            method VARCHAR(8) NOT NULL,
            path VARCHAR(255) NOT NULL,
            status INTEGER,
            ip VARCHAR(64),
            user_agent VARCHAR(255),
            duration_ms INTEGER
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_api_call_log_ts ON api_call_log(ts DESC)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_api_call_log_client_ts ON api_call_log(client_id, ts DESC)"))
