"""Public Participation Phase 1.1 -- create users table for Clerk-backed Tier 1 accounts.

This is the shared identity substrate for all four rooms (Lobby, Misread,
Motion Desk, Chamber). A row is lazily created on first authenticated
request once Clerk JWT verification passes (email + phone both verified).
The handle column is NULL until the user completes onboarding by claiming
one via POST /api/users/me/setup -- frontend gates posting on a non-null
handle.

tier values:
  'pending'      -- row exists but no handle yet (onboarding incomplete)
  'handled'      -- email + phone verified + handle claimed (Tier 1 fully active)
  'id_verified'  -- handled plus real ID via Persona/Stripe Identity (Tier 2)

status values:
  'active'    -- normal
  'suspended' -- cooldown (e.g. 24h after auto-hide)
  'banned'    -- account-killed; Clerk account closed, phone permanently blocked

anon_id is the opaque stable ID used in admin analytics views so handles
are not surfaced by default -- "User-47" instead of "@chad".
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clerk_user_id TEXT NOT NULL UNIQUE,
            handle TEXT UNIQUE,
            avatar_url TEXT,
            tier TEXT NOT NULL DEFAULT 'pending',
            anon_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'active',
            banned_at DATETIME,
            banned_reason TEXT,
            suspended_until DATETIME,
            created_at DATETIME NOT NULL DEFAULT (datetime('now')),
            updated_at DATETIME NOT NULL DEFAULT (datetime('now'))
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_users_clerk_user_id ON users(clerk_user_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_users_handle ON users(handle)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_users_status ON users(status)"
    ))
