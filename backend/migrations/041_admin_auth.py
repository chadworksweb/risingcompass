"""Multi-user admin auth — accounts, sessions, login attempts.

Replaces the single shared RC_ADMIN_KEY with per-user accounts (argon2id
hashes), server-issued opaque session tokens stored hashed in the DB,
and an append-only login attempt audit table that backs the rate limit
+ lockout policy. Cookies are HttpOnly + Secure + SameSite=Strict and
scoped to /api/admin so they never leak onto risingcompass.net.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username VARCHAR(64) NOT NULL UNIQUE,
            email TEXT,
            password_hash TEXT NOT NULL,
            role VARCHAR(20) NOT NULL DEFAULT 'admin',
            is_active BOOLEAN NOT NULL DEFAULT 1,
            failed_login_count INTEGER NOT NULL DEFAULT 0,
            locked_until DATETIME,
            last_login_at DATETIME,
            last_login_ip TEXT,
            created_at DATETIME NOT NULL DEFAULT (datetime('now')),
            updated_at DATETIME NOT NULL DEFAULT (datetime('now'))
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS admin_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash VARCHAR(64) NOT NULL UNIQUE,
            created_at DATETIME NOT NULL DEFAULT (datetime('now')),
            expires_at DATETIME NOT NULL,
            absolute_expires_at DATETIME NOT NULL,
            last_seen_at DATETIME NOT NULL DEFAULT (datetime('now')),
            ip TEXT,
            user_agent TEXT,
            revoked_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES admin_users(id) ON DELETE CASCADE
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_admin_sessions_user "
        "ON admin_sessions(user_id, revoked_at)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires "
        "ON admin_sessions(expires_at)"
    ))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS admin_login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            ip TEXT,
            user_agent TEXT,
            success BOOLEAN NOT NULL DEFAULT 0,
            reason TEXT,
            attempted_at DATETIME NOT NULL DEFAULT (datetime('now'))
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_admin_login_attempts_at "
        "ON admin_login_attempts(attempted_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_admin_login_attempts_ip "
        "ON admin_login_attempts(ip, attempted_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_admin_login_attempts_user "
        "ON admin_login_attempts(username, attempted_at DESC)"
    ))
