"""General-purpose public inquiry / contact form table.

Backs the reusable inquiry form (first used by the Album Charger's
"need more than 15 tracks?" link). PG-compatible (063+).
Base.metadata.create_all() makes this on fresh installs from the
GeneralInquiry model; this migration is the explicit, idempotent path for
the existing prod DB.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS general_inquiries (
            id SERIAL PRIMARY KEY,
            name TEXT,
            email TEXT,
            topic VARCHAR(40),
            subject TEXT,
            message TEXT NOT NULL,
            source VARCHAR(40),
            page_url TEXT,
            ip_address VARCHAR(45),
            status VARCHAR(20) DEFAULT 'new',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            handled_at TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_general_inquiries_status_created "
        "ON general_inquiries(status, created_at DESC)"
    ))
